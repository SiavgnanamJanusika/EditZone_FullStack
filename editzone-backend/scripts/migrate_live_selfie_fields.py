"""Additive migration for live-selfie state. Dry-run by default."""
import argparse
import asyncio

from app.db.mongodb import editors_col, users_col


async def migrate(apply: bool = False) -> dict:
    scanned = updated = grandfathered = 0
    async for profile in editors_col.find({}):
        scanned += 1
        fields = {}
        completed_user = await users_col.find_one({"_id": profile.get("user_id"), "registration_complete": True}, {"_id": 1})
        if "selfie_verified" not in profile:
            fields["selfie_verified"] = bool(completed_user and profile.get("identity_verification_status") == "verified")
        if "liveness_status" not in profile:
            fields["liveness_status"] = "legacy_verified" if fields.get("selfie_verified") else "waiting"
        if "verification_attempt_count" not in profile:
            fields["verification_attempt_count"] = 0
        if "last_verification_error" not in profile:
            fields["last_verification_error"] = None
        if profile.get("nic_ocr_verified") and not completed_user and profile.get("identity_verification_status") == "verified":
            fields["identity_verification_status"] = "nic_verified"
        if completed_user and fields.get("selfie_verified"):
            grandfathered += 1
        if fields:
            updated += 1
            if apply:
                await editors_col.update_one({"_id": profile["_id"]}, {"$set": fields})
    return {"scanned": scanned, "would_update" if not apply else "updated": updated, "grandfathered_completed_accounts": grandfathered}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(asyncio.run(migrate(args.apply)))
