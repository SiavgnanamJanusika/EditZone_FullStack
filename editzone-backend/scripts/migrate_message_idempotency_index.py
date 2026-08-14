"""Audit or safely migrate the chat client-message idempotency index."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.mongodb import (
    MESSAGE_IDEMPOTENCY_FILTER,
    MESSAGE_IDEMPOTENCY_INDEX,
    MESSAGE_IDEMPOTENCY_KEYS,
    client,
    ensure_message_idempotency_index,
    messages_col,
)


async def run(apply: bool) -> int:
    await client.admin.command("ping")
    missing = await messages_col.count_documents({"client_message_id": {"$exists": False}})
    null_or_missing = await messages_col.count_documents({"client_message_id": None})
    counts = {
        "total": await messages_col.count_documents({}),
        "missing": missing,
        "null": null_or_missing - missing,
        "empty": await messages_col.count_documents({"client_message_id": ""}),
    }
    duplicate_groups = await messages_col.aggregate([
        {"$match": MESSAGE_IDEMPOTENCY_FILTER},
        {"$group": {"_id": {"request_id": "$request_id", "sender_id": "$sender_id", "client_message_id": "$client_message_id"}, "count": {"$sum": 1}}},
        {"$match": {"count": {"$gt": 1}}},
        {"$count": "groups"},
    ]).to_list(1)
    summary = {
        "mode": "apply" if apply else "dry-run",
        "messages": counts,
        "duplicate_non_empty_id_groups": duplicate_groups[0]["groups"] if duplicate_groups else 0,
        "target_index": {
            "name": MESSAGE_IDEMPOTENCY_INDEX,
            "keys": MESSAGE_IDEMPOTENCY_KEYS,
            "partial_filter": MESSAGE_IDEMPOTENCY_FILTER,
        },
    }
    if apply:
        if summary["duplicate_non_empty_id_groups"]:
            summary["error"] = "Resolve duplicate non-empty client IDs before creating the unique index; no messages were changed."
            print(json.dumps(summary, indent=2, default=str))
            return 2
        summary["migration"] = await ensure_message_idempotency_index(messages_col)
    print(json.dumps(summary, indent=2, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply the index migration. Default is read-only dry-run.")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args.apply))
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
