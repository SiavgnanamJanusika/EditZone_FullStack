"""Single scheduler process. Run separately from Uvicorn API workers."""
import asyncio
import logging
from datetime import timedelta

from app.core.utils import now_utc
from app.config import settings
from app.db.mongodb import (
    failed_jobs_col, process_project_deadlines, purge_expired_project_media,
    worker_heartbeats_col,
)
from app.services.identity_verification_service import purge_expired_identity_documents
from app.services.malware_scanner import purge_expired_s3_uploads, scan_pending_s3_uploads, scan_pending_uploads
from app.services.financial_records import reconcile_payments

logger = logging.getLogger(__name__)

MAINTENANCE_JOBS = (purge_expired_project_media, purge_expired_s3_uploads, process_project_deadlines, purge_expired_identity_documents, reconcile_payments)


async def run_job(job, max_attempts: int = 3):
    for attempt in range(1, max_attempts + 1):
        try:
            return await job()
        except Exception as exc:
            logger.exception("Background job %s failed (attempt %s)", job.__name__, attempt)
            if attempt == max_attempts:
                await failed_jobs_col.insert_one({
                    "job": job.__name__, "attempts": attempt, "status": "failed",
                    "error_type": type(exc).__name__, "created_at": now_utc(),
                })
            else:
                await asyncio.sleep(2 ** attempt)


async def main():
    next_maintenance = now_utc()
    while True:
        await worker_heartbeats_col.update_one(
            {"worker": "scheduler"}, {"$set": {"updated_at": now_utc()}}, upsert=True,
        )
        await run_job(scan_pending_uploads)
        await run_job(scan_pending_s3_uploads)
        if now_utc() >= next_maintenance:
            for job in MAINTENANCE_JOBS:
                await run_job(job)
            next_maintenance = now_utc() + timedelta(minutes=settings.PAYMENT_RECONCILIATION_MINUTES)
        await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
