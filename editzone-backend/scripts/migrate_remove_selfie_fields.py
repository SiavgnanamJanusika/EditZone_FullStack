"""Safely remove obsolete selfie/face fields from editor documents.

Dry-run is the default. Pass --apply only after reviewing the reported counts.
No documents, users, NIC fields, S3 objects, or legacy selfie-session records are deleted.
"""

import argparse
import asyncio

from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings


OBSOLETE_FIELDS = (
    "live_selfie_url",
    "selfie_url",
    "selfie_s3_key",
    "selfie_verified",
    "selfie_verified_at",
    "selfie_dimensions",
    "face_match_similarity",
    "face_match_score",
    "liveness_passed",
    "liveness_status",
    "liveness_score",
    "nic_face_count",
    "nic_back_key",
)


async def migrate(apply: bool) -> dict:
    client = AsyncIOMotorClient(settings.MONGO_URI)
    editors_col = client[settings.MONGO_DB_NAME]["editors"]
    try:
        await client.admin.command("ping")
        counts = {
            field: await editors_col.count_documents({field: {"$exists": True}})
            for field in OBSOLETE_FIELDS
        }
        affected = await editors_col.count_documents({
            "$or": [{field: {"$exists": True}} for field in OBSOLETE_FIELDS]
        })
        promotable = await editors_col.count_documents(
            {"nic_ocr_verified": True, "identity_verification_status": {"$ne": "verified"}}
        )
        inconsistent = await editors_col.count_documents(
            {"identity_verification_status": "verified", "nic_ocr_verified": {"$ne": True}}
        )
        modified = promoted = moved_to_review = 0
        if apply:
            promote_result = await editors_col.update_many(
                {"nic_ocr_verified": True, "identity_verification_status": {"$ne": "verified"}},
                {"$set": {"identity_verification_status": "verified"}},
            )
            review_result = await editors_col.update_many(
                {"identity_verification_status": "verified", "nic_ocr_verified": {"$ne": True}},
                {"$set": {"identity_verification_status": "manual_review", "manual_review_reasons": ["NIC verification requires review after selfie-system removal"]}},
            )
            promoted = promote_result.modified_count
            moved_to_review = review_result.modified_count
            if affected:
                result = await editors_col.update_many(
                    {"$or": [{field: {"$exists": True}} for field in OBSOLETE_FIELDS]},
                    {"$unset": {field: "" for field in OBSOLETE_FIELDS}},
                )
                modified = result.modified_count
        return {"mode": "apply" if apply else "dry-run", "affected_documents": affected, "nic_verified_records_to_promote": promotable, "legacy_verified_records_requiring_nic_review": inconsistent, "modified_documents": modified, "promoted_documents": promoted, "moved_to_manual_review": moved_to_review, "field_counts": counts}
    finally:
        client.close()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Apply the field cleanup after reviewing dry-run output")
    args = parser.parse_args()
    print(await migrate(args.apply))


if __name__ == "__main__":
    asyncio.run(main())
