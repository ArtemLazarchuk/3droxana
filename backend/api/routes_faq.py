from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from assistant_core.chat import ChatServiceError, ChatSessionNotFound, process_chat

from ..auth.deps import get_current_user
from ..db.mongodb import get_database
from ..models import faq as faq_model
from ..schemas import faq as faq_schema

router = APIRouter(prefix="/api/faq", tags=["FAQ"])

@router.get("/", response_model=list[faq_schema.FAQOut])
async def read_all_faqs(db=Depends(get_database)):
    return await faq_model.get_all_faqs(db)

@router.get("/{id}", response_model=faq_schema.FAQOut)
async def read_faq(id: str, db=Depends(get_database)):
    faq = await faq_model.get_faq_by_id(db, id)
    if not faq:
        raise HTTPException(status_code=404, detail="FAQ not found")
    return faq

@router.post("/", response_model=str)
async def create_faq(faq: faq_schema.FAQCreate, db=Depends(get_database)):
    return await faq_model.create_faq(db, faq.dict())

@router.put("/{id}")
async def update_faq(id: str, faq: faq_schema.FAQUpdate, db=Depends(get_database)):
    await faq_model.update_faq(db, id, faq.dict())
    return {"message": "FAQ updated successfully"}

@router.delete("/{id}")
async def delete_faq(id: str, db=Depends(get_database)):
    await faq_model.delete_faq(db, id)
    return {"message": "FAQ deleted successfully"}

class ChatRequest(BaseModel):
    message: str
    sessionId: str
    userId: str

class ChatResponse(BaseModel):
    response: str
    link: str
    emotion: str
    title: str

@router.post("/chat", response_model=ChatResponse)
async def chat_with_faq(
    request: ChatRequest,
    db=Depends(get_database),
    current_user=Depends(get_current_user),
):
    if request.userId != current_user.get("id") and request.userId != current_user.get("_id"):
        raise HTTPException(status_code=403, detail="Не можна вести переписку від імені іншого користувача")
    try:
        result = await process_chat(db=db, message=request.message, session_id=request.sessionId)
    except ChatSessionNotFound:
        raise HTTPException(status_code=404, detail="Сесія не знайдена")
    except ChatServiceError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return ChatResponse(
        response=result["response"],
        link=result["link"],
        emotion=result["emotion"],
        title=result["title"],
    )

