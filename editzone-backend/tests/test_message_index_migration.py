import pytest

from app.db.mongodb import (
    MESSAGE_IDEMPOTENCY_FILTER,
    MESSAGE_IDEMPOTENCY_INDEX,
    MESSAGE_IDEMPOTENCY_KEYS,
    ensure_message_idempotency_index,
)


class FakeIndexes:
    def __init__(self, indexes):
        self.indexes = indexes
        self.dropped = []
        self.created = []

    async def index_information(self):
        return self.indexes

    async def drop_index(self, name):
        self.dropped.append(name)

    async def create_index(self, keys, **options):
        self.created.append((keys, options))
        return options.get("name")


@pytest.mark.asyncio
async def test_replaces_only_old_sparse_message_index():
    old_name = "request_id_1_sender_id_1_client_message_id_1"
    collection = FakeIndexes({
        "_id_": {"key": [("_id", 1)]},
        "request_id_1_created_at_1": {"key": [("request_id", 1), ("created_at", 1)]},
        old_name: {"key": MESSAGE_IDEMPOTENCY_KEYS, "unique": True, "sparse": True},
    })

    result = await ensure_message_idempotency_index(collection)

    assert result["dropped"] == [old_name]
    assert collection.created == [(MESSAGE_IDEMPOTENCY_KEYS, {
        "unique": True,
        "name": MESSAGE_IDEMPOTENCY_INDEX,
        "partialFilterExpression": MESSAGE_IDEMPOTENCY_FILTER,
    })]


@pytest.mark.asyncio
async def test_correct_message_index_is_idempotent():
    collection = FakeIndexes({
        "_id_": {"key": [("_id", 1)]},
        MESSAGE_IDEMPOTENCY_INDEX: {
            "key": MESSAGE_IDEMPOTENCY_KEYS,
            "unique": True,
            "partialFilterExpression": MESSAGE_IDEMPOTENCY_FILTER,
        },
    })

    await ensure_message_idempotency_index(collection)

    assert collection.dropped == []
