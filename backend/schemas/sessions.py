from pydantic import BaseModel, Field
from typing import List
from datetime import datetime
from typing import Optional

class Message(BaseModel):
    role: str  # "user" або "assistant"
    text: str
    # Лише для повідомлень асистента (після парсингу LLM); у користувача зазвичай None
    emotion: Optional[str] = None  # англ.: neutral, happy, sad, surprise, thinking
    link: Optional[str] = None
    title: Optional[str] = None  # короткий заголовок теми чату з відповіді моделі

class SessionBase(BaseModel):
    userId: str
    name: str  # нове поле
    messages: Optional[List[Message]] = []
    createdAt: datetime = Field(default_factory=datetime.utcnow)
    updatedAt: datetime = Field(default_factory=datetime.utcnow)

class SessionCreate(BaseModel):
    userId: str
    name: str
    messages: list[Message]
    updatedAt: Optional[datetime] = None

class SessionUpdate(BaseModel):
    messages: List[Message]
    updatedAt: datetime

class SessionOut(SessionBase):
    id: str = Field(..., alias="_id")

    model_config = {
        "from_attributes": True,
        "validate_by_name": True
    }
