import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from assistant_core.attachments import prepare_user_attachment
from assistant_core.chat import (
    ChatServiceError,
    ChatSessionNotFound,
    process_chat,
    stream_chat_events,
)
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
async def chat_with_faq(request: ChatRequest, db=Depends(get_database)):
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


@router.post("/chat/stream")
async def chat_with_faq_stream(request: ChatRequest, db=Depends(get_database)):
    """Потокова відповідь (SSE): status → delta → done | error."""

    async def event_iter():
        try:
            async for chunk in stream_chat_events(
                db, request.sessionId, request.message
            ):
                yield chunk
        except ChatSessionNotFound:
            yield (
                "data: "
                + json.dumps(
                    {"type": "error", "detail": "Сесія не знайдена"},
                    ensure_ascii=False,
                )
                + "\n\n"
            ).encode("utf-8")

    return StreamingResponse(
        event_iter(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat/stream/upload")
async def chat_with_faq_stream_upload(
    message: str = Form(""),
    sessionId: str = Form(...),
    userId: str = Form(...),
    file: UploadFile = File(...),
    db=Depends(get_database),
):
    """
    Те саме SSE, що /chat/stream, але з одним файлом (PDF або зображення).
    PDF обробляється локально; зображення — через vision-модель Together (див. TOGETHER_VISION_MODEL).
    """

    raw = await file.read()
    block, err = await prepare_user_attachment(file.filename or "file", raw)
    if err:
        raise HTTPException(status_code=400, detail=err)

    fname = file.filename or "file"

    async def event_iter():
        try:
            async for chunk in stream_chat_events(
                db,
                sessionId,
                message,
                attachment_block=block,
                attachment_filename=fname,
            ):
                yield chunk
        except ChatSessionNotFound:
            yield (
                "data: "
                + json.dumps(
                    {"type": "error", "detail": "Сесія не знайдена"},
                    ensure_ascii=False,
                )
                + "\n\n"
            ).encode("utf-8")

    return StreamingResponse(
        event_iter(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

