import logging

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException

from assistant_core.link_indexing import CHUNKS_COLLECTION, index_link_content

from ..db.mongodb import get_database
from ..models import links as link_model
from ..schemas import links as link_schema

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/links", tags=["Links"])

@router.get("/", response_model=list[link_schema.LinkOut])
async def read_all_links(db=Depends(get_database)):
    return await link_model.get_all_links(db)

@router.get("/{id}", response_model=link_schema.LinkOut)
async def read_link(id: str, db=Depends(get_database)):
    link = await link_model.get_link_by_id(db, id)
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")
    return link

@router.post("/", response_model=str)
async def create_link(link: link_schema.LinkCreate, db=Depends(get_database)):
    link_id = await link_model.create_link(db, link.dict())
    try:
        await index_link_content(db, link_id, link.url)
    except Exception:  # noqa: BLE001
        logger.exception("Після створення посилання не вдалося проіндексувати URL")
    return link_id

@router.post("/{id}/reindex")
async def reindex_link(id: str, db=Depends(get_database)):
    """Скидає чанки й знову знімає текст з URL (після зміни сторінки або помилки)."""
    link = await link_model.get_link_by_id(db, id)
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")
    await db[CHUNKS_COLLECTION].delete_many({"link_id": id})
    await db["links"].update_one(
        {"_id": ObjectId(id)},
        {"$unset": {"web_index_tried_at": "", "web_index_error": ""}},
    )
    await index_link_content(db, id, link["url"])
    count = await db[CHUNKS_COLLECTION].count_documents({"link_id": id})
    return {"chunk_count": count}

@router.delete("/{id}")
async def delete_link(id: str, db=Depends(get_database)):
    await link_model.delete_link(db, id)
    return {"message": "Link deleted successfully"}
