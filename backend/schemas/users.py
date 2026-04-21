from typing import Optional

from pydantic import BaseModel, EmailStr, Field

class UserBase(BaseModel):
    username: str
    tgNick: str
    email: EmailStr

class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str

class UserUpdate(BaseModel):
    tgNick: Optional[str] = None
    email: Optional[EmailStr] = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserOut(UserBase):
    id: str = Field(..., alias="_id")

    model_config = {
        "from_attributes": True,
        "validate_by_name": True
    }
