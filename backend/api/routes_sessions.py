from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException

from ..auth.deps import get_current_user
from ..db.mongodb import get_database
from ..models import sessions as session_model
from ..schemas import sessions as session_schema

router = APIRouter(prefix="/api/sessions", tags=["Sessions"])


def _user_id(user: dict) -> str:
    return user.get("id") or user.get("_id", "")


def _ensure_session_owner(session: dict, current_user: dict) -> None:
    if session.get("userId") != _user_id(current_user):
        raise HTTPException(status_code=403, detail="Немає доступу до цієї сесії")


@router.get("/", response_model=list[session_schema.SessionOut])
async def read_all_sessions(
    db=Depends(get_database),
    current_user=Depends(get_current_user),
):
    """Повертає сесії лише поточного користувача."""
    return await session_model.get_sessions_by_user(db, _user_id(current_user))


@router.get("/{id}", response_model=session_schema.SessionOut)
async def read_session(
    id: str,
    db=Depends(get_database),
    current_user=Depends(get_current_user),
):
    session = await session_model.get_session_by_id(db, id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    _ensure_session_owner(session, current_user)
    return session

@router.post("/", response_model=str)
async def create_or_update_session(
    session: session_schema.SessionCreate,
    db=Depends(get_database),
    current_user=Depends(get_current_user),
):
    if session.userId != _user_id(current_user):
        raise HTTPException(status_code=403, detail="Не можна створювати сесії для іншого користувача")
    existing = await db["sessions"].find_one({
        "userId": session.userId,
        "name": session.name
    })
    new_messages_dicts = [m.dict() for m in session.messages]
    if existing:
        # Об’єднати старі повідомлення (можливо dict-список) із новими
        combined_messages = existing.get("messages", []) + new_messages_dicts

        await db["sessions"].update_one(
            {"_id": existing["_id"]},
            {
                "$set": {
                    "messages": combined_messages,
                    "updatedAt": session.updatedAt or datetime.utcnow()
                }
            }
        )
        return str(existing["_id"])

    # Якщо не існує — створити нову сесію
    session_data = session.dict()
    session_data["messages"] = new_messages_dicts  # Перезаписати серіалізованими dict

    return await session_model.create_session(db, session_data)

@router.post("/newSession", response_model=str)
async def create_new_session(
    session: session_schema.SessionCreate,
    db=Depends(get_database),
    current_user=Depends(get_current_user),
):
    if session.userId != _user_id(current_user):
        raise HTTPException(status_code=403, detail="Не можна створювати сесії для іншого користувача")
    new_messages_dicts = [m.dict() for m in session.messages]
    session_data = session.dict()
    session_data["messages"] = new_messages_dicts
    return await session_model.create_session(db, session_data)


@router.put("/{id}", response_model=session_schema.SessionOut)
async def update_session(
    id: str,
    session: session_schema.SessionUpdate,
    db=Depends(get_database),
    current_user=Depends(get_current_user),
):
    existing = await session_model.get_session_by_id(db, id)
    if not existing:
        raise HTTPException(status_code=404, detail="Session not found")
    _ensure_session_owner(existing, current_user)
    await session_model.update_session(db, id, session.dict())
    updated = await session_model.get_session_by_id(db, id)
    return updated


@router.delete("/{id}")
async def delete_session(
    id: str,
    db=Depends(get_database),
    current_user=Depends(get_current_user),
):
    existing = await session_model.get_session_by_id(db, id)
    if not existing:
        raise HTTPException(status_code=404, detail="Session not found")
    _ensure_session_owner(existing, current_user)
    await session_model.delete_session(db, id)
    return {"message": "Session deleted successfully"}


@router.patch("/{id}/rename")
async def rename_session(
    id: str,
    body: dict,
    db=Depends(get_database),
    current_user=Depends(get_current_user),
):
    existing = await session_model.get_session_by_id(db, id)
    if not existing:
        raise HTTPException(status_code=404, detail="Сесію не знайдено")
    _ensure_session_owner(existing, current_user)
    new_name = body.get("name")
    if not new_name:
        raise HTTPException(status_code=400, detail="Нова назва не вказана")

    result = await db["sessions"].update_one(
        {"_id": ObjectId(id)},
        {"$set": {"name": new_name, "updatedAt": datetime.utcnow()}}
    )
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Назва не змінена")

    return {"status": "ok", "newName": new_name}


@router.get("/user/{user_id}", response_model=list[session_schema.SessionOut])
async def get_sessions_by_user(
    user_id: str,
    db=Depends(get_database),
    current_user=Depends(get_current_user),
):
    if user_id != _user_id(current_user):
        raise HTTPException(status_code=403, detail="Можна переглядати лише власну історію")
    return await session_model.get_sessions_by_user(db, user_id)
