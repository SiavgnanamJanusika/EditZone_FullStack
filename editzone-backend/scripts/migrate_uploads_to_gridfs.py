"""Copy legacy local uploads into MongoDB GridFS without deleting local files."""

import asyncio
import mimetypes
import os
import sys

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings


async def main():
    client = AsyncIOMotorClient(settings.MONGO_URI)
    db = client[settings.MONGO_DB_NAME]
    uploads_bucket = AsyncIOMotorGridFSBucket(db, bucket_name="uploads")
    upload_dir = settings.UPLOAD_DIR
    if not os.path.isdir(upload_dir):
        print("No local upload directory found.")
        return

    copied = 0
    skipped = 0
    for entry in os.scandir(upload_dir):
        if not entry.is_file() or entry.name == ".gitkeep":
            continue
        existing = await db["uploads.files"].find_one({"filename": entry.name}, {"_id": 1})
        if existing:
            skipped += 1
            continue
        with open(entry.path, "rb") as source:
            await uploads_bucket.upload_from_stream(
                entry.name,
                source,
                metadata={
                    "content_type": mimetypes.guess_type(entry.name)[0] or "application/octet-stream",
                    "source": "legacy_local_migration",
                },
            )
        copied += 1

    print(f"Copied {copied} files to MongoDB GridFS; skipped {skipped} existing files.")
    print("Local files were not deleted.")
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
