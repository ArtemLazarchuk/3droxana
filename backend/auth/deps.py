"""
Залежності для авторизації через JWT.
"""
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..config import JWT_ALGORITHM, JWT_SECRET_KEY
from ..db.mongodb import get_database
from ..models import users as user_model
from jose import JWTError, jwt

security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db=Depends(get_database),
) -> dict:
    """
    Декодує JWT з заголовка Authorization: Bearer <token>,
    повертає словник користувача. Кидає 401, якщо токен відсутній або невалідний.
    """
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Не авторизовано. Потрібен токен (Authorization: Bearer ...)",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Невірний токен")
    except JWTError:
        raise HTTPException(status_code=401, detail="Невірний або прострочений токен")

    user = await user_model.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Користувача не знайдено")

    return user
