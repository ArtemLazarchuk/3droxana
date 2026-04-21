from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from assistant_core.link_indexing import CHUNKS_COLLECTION

collection_name = "links"

def serialize_link(link) -> dict:
    link["_id"] = str(link["_id"])
    return link

async def get_all_links(db: AsyncIOMotorDatabase):
    links = await db[collection_name].find().to_list(1000)
    return [serialize_link(link) for link in links]

async def get_link_by_id(db: AsyncIOMotorDatabase, id: str):
    link = await db[collection_name].find_one({"_id": ObjectId(id)})
    return serialize_link(link) if link else None

async def create_link(db: AsyncIOMotorDatabase, data: dict):
    result = await db[collection_name].insert_one(data)
    return str(result.inserted_id)

async def delete_link(db: AsyncIOMotorDatabase, id: str):
    await db[CHUNKS_COLLECTION].delete_many({"link_id": id})
    await db[collection_name].delete_one({"_id": ObjectId(id)})
