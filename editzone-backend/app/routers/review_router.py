from fastapi import APIRouter, Depends, HTTPException

from app.db.mongodb import reviews_col, requests_col, editors_col, users_col
from app.schemas.schemas import CreateReviewBody
from app.core.security import require_user
from app.core.utils import serialize_doc, oid, now_utc
from app.core.accounts import ACTIVE_ACCOUNT_FILTER, ACTIVE_EDITOR_FILTER, account_not_available

router = APIRouter(prefix="/api/v1/reviews", tags=["Reviews"])


@router.post("", status_code=201)
async def create_review(body: CreateReviewBody, current_user: dict = Depends(require_user)):
    req_doc = await requests_col.find_one({"_id": oid(body.request_id)})
    if not req_doc:
        raise HTTPException(status_code=404, detail="Request not found")
    if req_doc["user_id"] != current_user["_id"]:
        raise HTTPException(status_code=403, detail="Not your project request")
    if req_doc["status"] != "completed":
        raise HTTPException(status_code=400, detail="You can only review a completed, delivered project")

    existing = await reviews_col.find_one({"request_id": body.request_id})
    if existing:
        raise HTTPException(status_code=409, detail="You already reviewed this project")

    doc = {
        "request_id": body.request_id,
        "user_id": current_user["_id"],
        "editor_user_id": req_doc["editor_user_id"],
        "rating": body.rating,
        "comment": body.comment,
        "created_at": now_utc(),
    }
    result = await reviews_col.insert_one(doc)
    doc["_id"] = result.inserted_id

    editor = await editors_col.find_one({"user_id": req_doc["editor_user_id"]})
    if editor:
        old_count = editor.get("rating_count", 0)
        old_avg = editor.get("rating_avg", 0)
        new_count = old_count + 1
        new_avg = round(((old_avg * old_count) + body.rating) / new_count, 2)
        await editors_col.update_one(
            {"_id": editor["_id"]},
            {"$set": {"rating_avg": new_avg, "rating_count": new_count}},
        )

    return serialize_doc(doc)


@router.get("/editor/{editor_id}")
async def get_editor_reviews(editor_id: str):
    editor = await editors_col.find_one({"_id": oid(editor_id), **ACTIVE_EDITOR_FILTER})
    if not editor:
        raise account_not_available()
    if not await users_col.find_one({"_id": editor.get("user_id"), "role": "editor", **ACTIVE_ACCOUNT_FILTER}, {"_id": 1}):
        raise account_not_available()
    reviews = await reviews_col.find({"editor_user_id": editor["user_id"]}).sort("created_at", -1).to_list(200)
    from app.core.utils import serialize_list
    return {"reviews": serialize_list(reviews)}
