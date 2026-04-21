from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from jose import jwt
from passlib.context import CryptContext

from ..auth.deps import get_current_user
from ..config import JWT_ACCESS_TOKEN_EXPIRE_MINUTES, JWT_ALGORITHM, JWT_SECRET_KEY
from ..db.mongodb import get_database
from ..models import users as user_model
from ..schemas import users as user_schema
from ..schemas.users import UserLogin

router = APIRouter(prefix="/api/users", tags=["Users"])

# Використовуємо sha256_crypt, щоб уникнути обмеження bcrypt у 72 байти
pwd_context = CryptContext(schemes=["sha256_crypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt


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
    if not stored_hash or not verify_password(login.password, stored_hash):
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
