import json
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jose import JWTError, jwt
from passlib.context import CryptContext
from passlib.exc import UnknownHashError

from ..auth.deps import get_current_user
from ..config import (
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES,
    JWT_ALGORITHM,
    JWT_SECRET_KEY,
    MAIL_FROM,
    MAIL_PASSWORD,
    MAIL_PORT,
    MAIL_SERVER,
    MAIL_USERNAME,
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES,
    PUBLIC_APP_BASE_URL,
)
from ..db.mongodb import get_database
from ..models import users as user_model
from ..schemas import users as user_schema
from ..schemas.users import UserLogin

router = APIRouter(prefix="/api/users", tags=["Users"])

# Нові паролі — sha256_crypt (немає ліміту 72 байти як у bcrypt). Перевірка також bcrypt — для старих записів у БД.
pwd_context = CryptContext(
    schemes=["sha256_crypt", "bcrypt"],
    deprecated="auto",
)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not hashed_password or not isinstance(hashed_password, str):
        return False
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except UnknownHashError:
        return False


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def _google_oauth_configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def _app_base_url(request: Request) -> str:
    if PUBLIC_APP_BASE_URL:
        return PUBLIC_APP_BASE_URL
    return str(request.base_url).rstrip("/")


def _google_redirect_uri(request: Request) -> str:
    return f"{_app_base_url(request)}/api/users/auth/google/callback"


def _google_oauth_state_token() -> str:
    return jwt.encode(
        {
            "purpose": "google_oauth",
            "exp": datetime.utcnow() + timedelta(minutes=15),
        },
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt


@router.get("/auth/google/status")
async def google_oauth_status():
    """Чи увімкнено вхід через Google (для кнопки на /auth)."""
    return {"enabled": _google_oauth_configured()}


@router.get("/auth/google/start")
async def google_oauth_start(request: Request):
    if not _google_oauth_configured():
        raise HTTPException(
            status_code=503,
            detail="Вхід через Google не налаштовано (немає GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET).",
        )
    state = _google_oauth_state_token()
    q = urlencode(
        {
            "client_id": GOOGLE_CLIENT_ID,
            "redirect_uri": _google_redirect_uri(request),
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "prompt": "select_account",
        }
    )
    return RedirectResponse(
        url=f"https://accounts.google.com/o/oauth2/v2/auth?{q}",
        status_code=302,
    )


@router.get("/auth/google/callback")
async def google_oauth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db=Depends(get_database),
):
    if error:
        return RedirectResponse(url=f"/auth?oauth_error={error}", status_code=302)
    if not code or not state:
        return RedirectResponse(url="/auth?oauth_error=missing_code", status_code=302)
    if not _google_oauth_configured():
        return RedirectResponse(url="/auth?oauth_error=not_configured", status_code=302)

    try:
        payload = jwt.decode(state, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        if payload.get("purpose") != "google_oauth":
            raise JWTError()
    except JWTError:
        return RedirectResponse(url="/auth?oauth_error=invalid_state", status_code=302)

    redirect_uri = _google_redirect_uri(request)
    try:
        async with httpx.AsyncClient() as client:
            tr = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30.0,
            )
        if tr.status_code != 200:
            return RedirectResponse(
                url="/auth?oauth_error=token_exchange", status_code=302
            )
        g_tokens = tr.json()
        g_access = g_tokens.get("access_token")
        if not g_access:
            return RedirectResponse(
                url="/auth?oauth_error=no_access_token", status_code=302
            )
        async with httpx.AsyncClient() as client:
            ui = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {g_access}"},
                timeout=20.0,
            )
        if ui.status_code != 200:
            return RedirectResponse(url="/auth?oauth_error=userinfo", status_code=302)
        info = ui.json()
    except httpx.HTTPError:
        return RedirectResponse(url="/auth?oauth_error=network", status_code=302)

    google_sub = str(info.get("id") or "")
    email = (info.get("email") or "").strip().lower()
    if not google_sub or not email:
        return RedirectResponse(url="/auth?oauth_error=no_email", status_code=302)
    if not info.get("verified_email"):
        return RedirectResponse(url="/auth?oauth_error=email_not_verified", status_code=302)

    name = (info.get("name") or "").strip() or email.split("@", 1)[0]
    g_picture = (info.get("picture") or "").strip() or None

    user_raw = await user_model.get_user_by_email(db, email)
    if user_raw:
        existing_sub = user_raw.get("google_sub")
        if existing_sub and existing_sub != google_sub:
            return RedirectResponse(
                url="/auth?oauth_error=email_linked_other_google", status_code=302
            )
        if not existing_sub:
            await user_model.update_user(
                db, str(user_raw["_id"]), {"google_sub": google_sub}
            )
            user_raw = await user_model.get_user_by_email(db, email)
    else:
        new_doc = {
            "username": name[:120],
            "email": email,
            "tgNick": "",
            "google_sub": google_sub,
        }
        await user_model.create_user(db, new_doc)
        user_raw = await user_model.get_user_by_email(db, email)
        if not user_raw:
            return RedirectResponse(
                url="/auth?oauth_error=create_failed", status_code=302
            )

    if g_picture and user_raw and user_raw.get("picture") != g_picture:
        await user_model.update_user(
            db, str(user_raw["_id"]), {"picture": g_picture}
        )
        user_raw = await user_model.get_user_by_email(db, email)

    user = user_model.serialize_user(user_raw)
    access_token = create_access_token(data={"sub": user["id"], "email": user["email"]})

    user_json = json.dumps(user, ensure_ascii=False)
    script_user_literal = json.dumps(user_json)
    script_token_literal = json.dumps(access_token)
    html = f"""<!DOCTYPE html>
<html lang="uk">
<head><meta charset="utf-8"><title>Вхід…</title></head>
<body>
<script>
localStorage.setItem("access_token", {script_token_literal});
localStorage.setItem("user", {script_user_literal});
window.location.replace("/chat");
</script>
<noscript><p>Увімкніть JavaScript і відкрийте <a href="/chat">чат</a>.</p></noscript>
</body>
</html>"""
    return HTMLResponse(content=html)


@router.get("/", response_model=list[user_schema.UserOut])
async def read_all_users(
    db=Depends(get_database),
    current_user=Depends(get_current_user),
):
    return await user_model.get_all_users(db)


@router.get("/{id}", response_model=user_schema.UserOut)
async def read_user(
    id: str,
    db=Depends(get_database),
    current_user=Depends(get_current_user),
):
    user = await user_model.get_user_by_id(db, id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.post("/login")
async def login_user(login: UserLogin, db=Depends(get_database)):
    # Знаходимо користувача за email
    user_raw = await user_model.get_user_by_email(db, login.email)
    if not user_raw:
        raise HTTPException(status_code=401, detail="Невірна пошта або пароль")

    stored_hash = user_raw.get("password")
    if not stored_hash:
        raise HTTPException(
            status_code=401,
            detail="Для цього акаунта пароль не задано — увійдіть через Google.",
        )
    if not verify_password(login.password, stored_hash):
        raise HTTPException(status_code=401, detail="Невірна пошта або пароль")

    # Серіалізуємо користувача перед віддачею (без пароля)
    user = user_model.serialize_user(user_raw)

    # Створюємо JWT-токен
    access_token = create_access_token(
        data={"sub": user["id"], "email": user["email"]}
    )

    return {
        "message": "Успішний вхід",
        "user": user,
        "access_token": access_token,
        "token_type": "bearer",
    }


@router.post("/register", response_model=str)
async def register_user(user: user_schema.UserCreate, db=Depends(get_database)):
    # Перевірка, чи email вже існує
    existing_user = await db["users"].find_one({"email": user.email})
    if existing_user:
        raise HTTPException(status_code=400, detail="Користувач з таким email вже існує")

    user_data = user.dict()
    user_data["password"] = hash_password(user_data["password"])

    return await user_model.create_user(db, user_data)


@router.put("/{id}")
async def update_user(
    id: str,
    user: user_schema.UserUpdate,
    db=Depends(get_database),
    current_user=Depends(get_current_user),
):
    if id != current_user.get("id") and id != current_user.get("_id"):
        raise HTTPException(status_code=403, detail="Можна оновлювати лише власний профіль")
    await user_model.update_user(db, id, {k: v for k, v in user.dict().items() if v is not None})
    return {"message": "User updated successfully"}


@router.delete("/{id}")
async def delete_user(
    id: str,
    db=Depends(get_database),
    current_user=Depends(get_current_user),
):
    if id != current_user.get("id") and id != current_user.get("_id"):
        raise HTTPException(status_code=403, detail="Можна видаляти лише власний акаунт")
    await user_model.delete_user(db, id)
    return {"message": "User deleted successfully"}


# ---------------------------------------------------------------------------
# Скидання пароля
# ---------------------------------------------------------------------------

def _send_reset_email(to_email: str, reset_link: str) -> None:
    """Відправляє HTML-листа зі скиданням пароля через Gmail SMTP."""
    if not MAIL_USERNAME or not MAIL_PASSWORD:
        raise RuntimeError("MAIL_USERNAME / MAIL_PASSWORD не задано у конфігурації")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Скидання пароля — KPI Assistant"
    msg["From"] = MAIL_FROM or MAIL_USERNAME
    msg["To"] = to_email

    text_body = f"Для скидання пароля перейдіть за посиланням:\n{reset_link}\n\nПосилання дійсне 15 хвилин."
    html_body = f"""<!DOCTYPE html>
<html lang="uk">
<head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;background:#0d0d0d;color:#eee;padding:32px;">
  <div style="max-width:480px;margin:auto;background:#1a1a1a;border-radius:12px;padding:32px;">
    <h2 style="color:#a78bfa;margin-top:0">Скидання пароля</h2>
    <p>Ми отримали запит на скидання пароля для вашого акаунта.</p>
    <p>Натисніть кнопку нижче (посилання дійсне <strong>15 хвилин</strong>):</p>
    <a href="{reset_link}"
       style="display:inline-block;margin:16px 0;padding:12px 28px;background:#7c3aed;
              color:#fff;text-decoration:none;border-radius:8px;font-weight:bold;">
      Скинути пароль
    </a>
    <p style="color:#888;font-size:13px;">Якщо ви не надсилали цей запит — просто проігноруйте листа.</p>
  </div>
</body>
</html>"""

    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP(MAIL_SERVER, MAIL_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(MAIL_USERNAME, MAIL_PASSWORD)
        smtp.sendmail(msg["From"], [to_email], msg.as_string())


@router.post("/forgot-password")
async def forgot_password(
    body: user_schema.ForgotPasswordRequest,
    request: Request,
    db=Depends(get_database),
):
    """Ініціює скидання пароля: відправляє лист із посиланням."""
    user_raw = await user_model.get_user_by_email(db, body.email)
    # Завжди повертаємо 200, щоб не розкривати чи email зареєстровано
    if not user_raw:
        return {"message": "Якщо такий email зареєстровано, лист надіслано."}

    stored_hash = user_raw.get("password")
    if not stored_hash:
        # Акаунт через Google OAuth — пароль не задано
        return {"message": "Якщо такий email зареєстровано, лист надіслано."}

    user = user_model.serialize_user(user_raw)
    token = jwt.encode(
        {
            "purpose": "password_reset",
            "sub": user["id"],
            "email": user["email"],
            "exp": datetime.utcnow() + timedelta(minutes=PASSWORD_RESET_TOKEN_EXPIRE_MINUTES),
        },
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )

    base = _app_base_url(request)
    reset_link = f"{base}/reset-password?token={token}"

    try:
        _send_reset_email(body.email, reset_link)
    except Exception as exc:
        # Логуємо, але не відкриваємо деталі клієнту
        print(f"[forgot-password] email error: {exc}")
        raise HTTPException(status_code=500, detail="Помилка відправки листа. Спробуйте пізніше.")

    return {"message": "Якщо такий email зареєстровано, лист надіслано."}


@router.post("/reset-password")
async def reset_password(
    body: user_schema.ResetPasswordRequest,
    db=Depends(get_database),
):
    """Перевіряє токен і встановлює новий пароль."""
    try:
        payload = jwt.decode(body.token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        if payload.get("purpose") != "password_reset":
            raise JWTError()
    except JWTError:
        raise HTTPException(status_code=400, detail="Посилання недійсне або прострочене.")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=400, detail="Посилання недійсне.")

    if len(body.new_password) < 6:
        raise HTTPException(status_code=422, detail="Пароль має бути не менше 6 символів.")

    new_hash = hash_password(body.new_password)
    await user_model.update_user(db, user_id, {"password": new_hash})
    return {"message": "Пароль успішно змінено."}
