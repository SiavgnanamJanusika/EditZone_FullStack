"""Copy GridFS uploads from the retired local MongoDB database into Atlas."""

import asyncio
import os
import sys

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings


async def main():
    source_client = AsyncIOMotorClient(
        "mongodb://127.0.0.1:27017",
        serverSelectionTimeoutMS=5000,
    )
    target_client = AsyncIOMotorClient(settings.MONGO_URI, serverSelectionTimeoutMS=10000)
    source_db = source_client[settings.MONGO_DB_NAME]
    target_db = target_client[settings.MONGO_DB_NAME]
    source_bucket = AsyncIOMotorGridFSBucket(source_db, bucket_name="uploads")
    target_bucket = AsyncIOMotorGridFSBucket(target_db, bucket_name="uploads")

    copied = 0
    skipped = 0
    async for record in source_db["uploads.files"].find({}).sort("uploadDate", 1):
        existing = await target_db["uploads.files"].find_one(
            {"filename": record["filename"]},
            {"_id": 1},
        )
        if existing:
            skipped += 1
            continue
        source = await source_bucket.open_download_stream(record["_id"])
        contents = await source.read()
        await target_bucket.upload_from_stream(
            record["filename"],
            contents,
            metadata=record.get("metadata") or {},
        )
        copied += 1

    print(f"Copied {copied} GridFS files to Atlas; skipped {skipped} existing files.")
    source_client.close()
    target_client.close()


if __name__ == "__main__":
    asyncio.run(main())
