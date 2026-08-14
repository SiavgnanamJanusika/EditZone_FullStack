"""Archive the retired manual-withdrawal data without deleting records.

Dry-run is the default. With --apply, obsolete fields are unset from user/editor
documents and the withdrawal-only `payouts` collection is renamed to a legacy
archive. Project payments and financial ledger records are never modified.
"""

import argparse
import asyncio

from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings


OBSOLETE_FIELDS = (
    "withdrawal_amount",
    "withdrawal_status",
    "withdrawal_requested_at",
    "withdrawal_processed_at",
    "withdrawal_method",
    "withdrawal_history",
    "pending_withdrawal_balance",
)
SOURCE_COLLECTION = "payouts"
ARCHIVE_COLLECTION = "legacy_withdrawal_requests_archive"


async def migrate(apply: bool) -> dict:
    client = AsyncIOMotorClient(settings.MONGO_URI)
    database = client[settings.MONGO_DB_NAME]
    try:
        await client.admin.command("ping")
        collection_names = await database.list_collection_names()
        source_exists = SOURCE_COLLECTION in collection_names
        archive_exists = ARCHIVE_COLLECTION in collection_names
        request_count = await database[SOURCE_COLLECTION].count_documents({}) if source_exists else 0
        field_counts = {}
        affected_documents = 0
        for collection_name in ("users", "editors"):
            collection = database[collection_name]
            counts = {
                field: await collection.count_documents({field: {"$exists": True}})
                for field in OBSOLETE_FIELDS
            }
            affected = await collection.count_documents({
                "$or": [{field: {"$exists": True}} for field in OBSOLETE_FIELDS]
            })
            field_counts[collection_name] = counts
            affected_documents += affected

        modified_documents = 0
        archived = False
        if apply:
            if source_exists and archive_exists:
                raise RuntimeError(
                    f"Both {SOURCE_COLLECTION} and {ARCHIVE_COLLECTION} exist; resolve the archive name before applying"
                )
            for collection_name in ("users", "editors"):
                result = await database[collection_name].update_many(
                    {"$or": [{field: {"$exists": True}} for field in OBSOLETE_FIELDS]},
                    {"$unset": {field: "" for field in OBSOLETE_FIELDS}},
                )
                modified_documents += result.modified_count
            if source_exists:
                await database[SOURCE_COLLECTION].rename(ARCHIVE_COLLECTION, dropTarget=False)
                archived = True

        return {
            "mode": "apply" if apply else "dry-run",
            "withdrawal_request_records": request_count,
            "source_collection_exists": source_exists,
            "archive_collection_exists": archive_exists,
            "affected_documents": affected_documents,
            "modified_documents": modified_documents,
            "collection_archived": archived,
            "field_counts": field_counts,
        }
    finally:
        client.close()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(await migrate(args.apply))


if __name__ == "__main__":
    asyncio.run(main())
