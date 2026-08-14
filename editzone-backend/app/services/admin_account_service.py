"""Reversible admin account lifecycle; financial and project records are preserved."""

from fastapi import HTTPException

from app.core.utils import now_utc
from app.db.mongodb import account_deletion_audit_logs_col, auth_sessions_col, editors_col, users_col

DELETED_MESSAGE = "This account has been deleted by the administrator. Please contact support."


async def admin_delete_account(account: dict, admin: dict, *, reason: str, ip_address: str) -> dict:
    if account.get("role") not in {"user", "editor"}:
        raise HTTPException(status_code=403, detail="Administrator accounts cannot be deleted")
    if account.get("status") == "deleted" or account.get("is_deleted") is True:
        raise HTTPException(status_code=409, detail="Account is already deleted")
    now = now_utc()
    result = await users_col.update_one(
        {"_id": account["_id"], "role": account["role"], "status": {"$ne": "deleted"}},
        {"$set": {
            "status": "deleted", "account_status": "deleted", "is_deleted": True,
            "is_active": False, "deleted_at": now, "deleted_by": admin["_id"],
            "deletion_reason": reason, "token_valid_after": now,
        }},
    )
    if result.modified_count != 1:
        raise HTTPException(status_code=409, detail="Account state changed before deletion")
    await auth_sessions_col.update_many(
        {"user_id": account["_id"], "revoked_at": None},
        {"$set": {"revoked_at": now, "revoke_reason": "admin_account_deleted"}},
    )
    if account["role"] == "editor":
        await editors_col.update_one(
            {"user_id": account["_id"]},
            {"$set": {"status": "deleted", "is_deleted": True, "is_active": False, "is_available": False, "deleted_at": now}},
        )
    await account_deletion_audit_logs_col.insert_one({
        "admin_id": admin["_id"], "account_id": account["_id"],
        "account_role": account["role"], "action": "admin_delete",
        "reason": reason, "created_at": now, "ip_address": ip_address,
    })
    return {"status": "deleted", "deleted_at": now}


async def admin_restore_account(account: dict, admin: dict, *, reason: str, ip_address: str) -> dict:
    if account.get("role") not in {"user", "editor"}:
        raise HTTPException(status_code=403, detail="Administrator accounts cannot be restored here")
    if account.get("status") != "deleted" and account.get("is_deleted") is not True:
        raise HTTPException(status_code=409, detail="Account is not deleted")
    now = now_utc()
    result = await users_col.update_one(
        {"_id": account["_id"], "role": account["role"], "$or": [{"status": "deleted"}, {"is_deleted": True}]},
        {
            "$set": {
                "status": "active", "account_status": "active", "is_deleted": False,
                "is_active": True, "restored_at": now, "restored_by": admin["_id"],
                "token_valid_after": now,
            },
            "$unset": {"deleted_at": "", "deleted_by": "", "deletion_reason": ""},
        },
    )
    if result.modified_count != 1:
        raise HTTPException(status_code=409, detail="Account state changed before restoration")
    # Old sessions remain revoked; restoration never resurrects credentials.
    if account["role"] == "editor":
        await editors_col.update_one(
            {"user_id": account["_id"]},
            {
                "$set": {"status": "active", "is_deleted": False, "is_active": True, "is_available": True, "restored_at": now},
                "$unset": {"deleted_at": ""},
            },
        )
    await account_deletion_audit_logs_col.insert_one({
        "admin_id": admin["_id"], "account_id": account["_id"],
        "account_role": account["role"], "action": "admin_restore",
        "reason": reason, "created_at": now, "ip_address": ip_address,
    })
    return {"status": "active", "restored_at": now}
