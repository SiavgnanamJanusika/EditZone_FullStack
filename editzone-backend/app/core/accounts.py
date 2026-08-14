from fastapi import HTTPException


ACTIVE_ACCOUNT_FILTER = {
    "is_deleted": {"$ne": True},
    "status": {"$ne": "deleted"},
}

ACTIVE_EDITOR_FILTER = {
    "deleted": {"$ne": True},
    "is_deleted": {"$ne": True},
    "status": {"$ne": "deleted"},
}


def account_not_available() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "success": False,
            "code": "ACCOUNT_NOT_AVAILABLE",
            "message": "This account is no longer available.",
        },
    )


def is_deleted_account(document: dict | None) -> bool:
    return bool(
        not document
        or document.get("is_deleted") is True
        or document.get("deleted") is True
        or document.get("status") == "deleted"
    )
