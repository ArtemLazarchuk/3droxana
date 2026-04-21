from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

collection_name = "users"

def serialize_user(user) -> dict:
    # Нормалізуємо id у два ключі: "_id" (як у Mongo) та "id" (зручно для JWT / фронта)
    string_id = str(user["_id"])
    user["_id"] = string_id
    user["id"] = string_id
    # Не віддавати пароль у відповідях
    user.pop("password", None)
    return user


async def get_all_users(db: AsyncIOMotorDatabase):
    users = await db[collection_name].find().to_list(1000)
    return [serialize_user(u) for u in users]


async def get_user_by_id(db: AsyncIOMotorDatabase, id: str):
    user = await db[collection_name].find_one({"_id": ObjectId(id)})
    return serialize_user(user) if user else None


async def get_user_by_email(db: AsyncIOMotorDatabase, email: str):
    """
    Повертає сирий документ користувача за email (разом із полем password),
    щоб логіка хешування могла перевірити пароль.
    """
    return await db[collection_name].find_one({"email": email})


async def create_user(db: AsyncIOMotorDatabase, data: dict):
    result = await db[collection_name].insert_one(data)
    return str(result.inserted_id)


async def update_user(db: AsyncIOMotorDatabase, id: str, data: dict):
    await db[collection_name].update_one({"_id": ObjectId(id)}, {"$set": data})


async def delete_user(db: AsyncIOMotorDatabase, id: str):
    await db[collection_name].delete_one({"_id": ObjectId(id)})
