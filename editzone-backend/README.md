# EditZone — Backend

FastAPI + MongoDB (Motor) + Socket.IO backend for the EditZone video-editor marketplace.

## PayHere callback

`PAYHERE_NOTIFY_URL` must be a public HTTPS URL ending in
`/api/v1/payments/payhere/notify`. PayHere cannot notify localhost. For local
sandbox testing, expose port 8000 with ngrok or cloudflared and register that
exact URL in the PayHere sandbox dashboard. Browser completion is never proof
of payment; only the verified form-encoded callback changes financial state.

## Tech
FastAPI · Motor (MongoDB + GridFS) · JWT (python-jose) · bcrypt==4.0.1 (via passlib) · python-socketio

## Setup

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # edit MONGO_URI, JWT_SECRET_KEY, etc.
```

Make sure MongoDB is running locally, or point `MONGO_URI` at Atlas.

Run the development server. The exported `app` includes both FastAPI and
Socket.IO, so the standard command serves REST, docs, and chat networking:

```bash
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Local development can run one backend process without Redis; Socket.IO then uses
its in-memory manager and readiness reports a degraded service. Start Redis (or
use `docker compose up --build`) before using multiple backend workers. Redis is
mandatory in staging and production, where startup fails clearly if unavailable.

API docs: http://localhost:8000/docs
Health: http://localhost:8000/api/v1/health
Liveness: http://localhost:8000/api/v1/health/live
Readiness: http://localhost:8000/api/v1/health/ready

Run the single scheduler separately (never once per Uvicorn worker):

```bash
python -m app.worker
```

Media uploads use `uploading → uploaded → scanning → ready|rejected|failed|cancelled`.
When `MEDIA_SCANNER_ENABLED=true`, run both the scheduler above and ClamAV
(`CLAMAV_HOST`/`CLAMAV_PORT` or `MEDIA_SCANNER_SOCKET`). Temporary scanner
failures stop after `MEDIA_SCAN_MAX_ATTEMPTS`; users may explicitly retry.
When scanning is explicitly disabled, signature/MIME/size validation still runs
and successfully stored media becomes ready immediately. Processing state is
available from `GET /api/v1/media/{media_id}/status`.

## NIC and live-selfie verification

Editor registration cannot be completed until (1) the entered Sri Lankan NIC
matches AWS Textract OCR from the uploaded front image and (2) a camera-only
live selfie passes the randomized liveness challenge and AWS Rekognition face
comparison. The NIC back is not requested. Uncertain face matches enter manual
admin review instead of being permanently rejected.

Configure `AWS_S3_BUCKET_NAME`, `AWS_REGION`, `AWS_TEXTRACT_MIN_CONFIDENCE`,
`AWS_REKOGNITION_SIMILARITY_THRESHOLD`, `SELFIE_MAX_UPLOAD_MB`,
`SELFIE_VERIFICATION_MAX_ATTEMPTS`, and `SELFIE_LIVENESS_TIMEOUT_SECONDS`.
Prefer an EC2/ECS IAM role; local development may use `AWS_ACCESS_KEY_ID` and
`AWS_SECRET_ACCESS_KEY`. Never put AWS credentials in the frontend.

The IAM principal needs only `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject`,
`textract:DetectDocumentText`, `rekognition:DetectFaces`, and
`rekognition:CompareFaces`. Restrict S3 resources to the private bucket's
`editzone/identity-documents/*` and `live-selfies/*` object ARNs. Block public
access; MongoDB stores object keys, not image bytes or signed URLs.

The authenticated workflow is:

- `POST /api/v1/verification/nic` — OCR and securely store the NIC front image.
- `GET /api/v1/editor/identity/status` — return the staged verification state.
- `POST /api/v1/editor/selfie/session` — issue a randomized, expiring liveness session.
- `POST /api/v1/editor/selfie` — submit a camera frame and liveness evidence once.
- `POST /api/v1/editor/selfie/retry` — invalidate open sessions before retrying.
- `GET /api/v1/editor/selfie/status` — return the authenticated editor's selfie state.
- `GET /api/v1/admin/editors/identity-review` — admin-only uncertain-match queue.

Run the additive migration in dry-run mode, review its counts, then apply it. It
does not delete records or S3 objects and preserves completed legacy accounts:

```bash
python -m scripts.migrate_live_selfie_fields
python -m scripts.migrate_live_selfie_fields --apply
```

For Swagger testing, start the API and open `/docs`; authenticate first so the
Secure/HttpOnly cookie accompanies each request. Verify the NIC, create a selfie
session, then submit multipart fields `file`, `session_id`, `capture_token`,
`captured_at`, `face_count`, and `liveness_events` with
`X-Capture-Source: camera`. In a browser use `localhost` or HTTPS, allow camera
permission, complete the displayed action, capture, and confirm the final status
has both `nic_front_verified` and `selfie_verified` set to true.

Create your first admin account:

```bash
python scripts/create_admin.py
```

## Docker

```bash
docker compose up --build
```

## Folder Structure

```
app/
  main.py               FastAPI app + Socket.IO ASGI mount
  config.py              pydantic-settings, reads .env
  core/
    security.py          JWT, bcrypt, RBAC dependencies
    validators.py        NIC / phone / email / file-type validation
    utils.py              ObjectId + Mongo doc serialization helpers
  db/
    mongodb.py             Motor client + collections + indexes
  routers/
    auth_router.py         register/login/complete-profile/forgot-reset/OTP
    user_router.py
    editor_router.py       listing, search/filter, profile CRUD
    request_router.py      project requests: create/accept/reject/deliver
    chat_router.py         message history (REST) — live chat is Socket.IO
    payment_router.py      PayHere authorization/capture + commission split
    review_router.py       1–5 star rating + 100+ char review
    admin_router.py        user/editor/payment mgmt, delivery verification
    upload_router.py       MongoDB GridFS upload and streaming
    notification_router.py
  sockets/
    socket_manager.py      Socket.IO events: connect (JWT auth), join_chat,
                            send_message, typing, notification broadcast
  schemas/                 Pydantic request/response models
scripts/
  create_admin.py          interactive admin account creator
nginx/editzone.conf         production reverse-proxy config
Dockerfile, docker-compose.yml
```

## MongoDB GridFS uploads

Uploaded images, videos, audio, archives, and documents are stored in the configured MongoDB
database using the `uploads.files` and `uploads.chunks` GridFS collections. The API preserves the
`/api/v1/uploads/file/{filename}` URL contract and streams the bytes from GridFS; it does not use a
local filesystem fallback.

## PayHere Sandbox Payment Protection

Set `PAYHERE_MERCHANT_ID`, `PAYHERE_MERCHANT_SECRET`, `PAYHERE_APP_ID`,
`PAYHERE_APP_SECRET`, `PAYHERE_NOTIFY_URL`, `PAYHERE_MODE=sandbox`, and
`PAYHERE_CURRENCY=LKR` in the backend `.env`. The notify URL must be a public
HTTPS URL during a real sandbox checkout; PayHere cannot call localhost.
`PAYHERE_SANDBOX` must remain `true`; the backend rejects live mode. The App
credentials need PayHere Capture and Refund permissions. The notify URL must be
a public HTTPS endpoint because PayHere cannot call localhost.

The React app redirects to PayHere's Sandbox Authorize page. EditZone never
receives or stores PAN, CVV, expiry, or bank details. The API locks the project,
amount, fee, currency, and owner before generating the hash. The status changes
from `PENDING` to `AUTHORIZED` only after the signed notification is verified and
the stored amount/currency match. After final delivery, only the JWT-authenticated
project owner can approve work. Approval atomically calls PayHere's Capture API
once and changes the status to `CAPTURED`. PayHere handles settlement to the bank
account registered on the Merchant Account.

The product calls this “Payment Protection” or “Hold Until Approval.” It does not
claim to provide legally regulated escrow. `platform_fee_amount` and
`editor_earning_amount` are stored separately, and editor earnings become
available only after capture.

API endpoints:

- `POST /api/v1/payments/payhere/initiate` — create authorization
- `POST /api/v1/payments/payhere/create` — chat checkout; accepts only `request_id`
- `POST /api/v1/payments/payhere/notify` — verified PayHere callback
- `POST /api/v1/payments/{request_id}/approve` — owner approval and capture
- `POST /api/v1/payments/{request_id}/refund` — owner/admin refund
- `GET /api/v1/payments/status/{request_id}` — protected payment status
- `GET /api/v1/payments/{order_id}/status` — owner/editor/admin order status
- `GET /api/v1/payments/earnings/mine` — editor earnings

For local PayHere callbacks, run the backend on port 8000 and expose it with a
secure tunnel, for example `ngrok http 8000` or
`cloudflared tunnel --url http://localhost:8000`. Set `PAYHERE_NOTIFY_URL` to
the resulting public HTTPS origin plus `/api/v1/payments/payhere/notify`; never
commit a temporary tunnel hostname. Browser return routes are
`/payment/success`, `/payment/cancel`, and `/payment/pending`. These pages only
read backend state and cannot mark an order paid.

## Core Business Logic

- **Roles**: `user`, `editor`, `admin` — enforced via `require_user` / `require_editor` / `require_admin` FastAPI dependencies (RBAC).
- **Registration flow**: `/auth/register` creates an inactive account → hashed email OTP verification → cookie session → profile completion. Unverified accounts are denied by the shared auth dependency.
- **Request lifecycle**: `pending → accepted/rejected → in_progress (payment authorized) → delivered (editor uploads) → completed (owner approves and payment is captured)`.
- **Payment Protection**: PayHere authorizes the amount first. Only the authenticated project owner can capture it after final delivery.
- **Payments**: PayHere Sandbox Authorize/Capture/Refund APIs with backend hash generation, verified callbacks, idempotent state transitions, duplicate protection, and authenticated status checks.
- **Real-time chat**: Redis is the Socket.IO message manager for cross-worker rooms. The browser sends the HttpOnly authentication cookie. Attachments use an owned `upload_id` from the same project; external URLs are not trusted attachments.
- **Chat abuse controls**: staging/production use Redis-backed per-user and per-room fixed-window limits for connections, messages, and typing events. Development uses equivalent process-local limits. Configure the `CHAT_*_RATE_LIMIT` values and `CHAT_RATE_LIMIT_WINDOW_SECONDS`.
- **File uploads**: `/api/v1/uploads` requires an explicit purpose, validates name/type/signature/size, quarantines content until ClamAV approves it, and chunk-streams to GridFS without buffering an entire video. Private access uses short-lived links and membership checks. View-once videos use a single atomically redeemable, server-proxied capability; ordinary media links and replay attempts are rejected.

## Security Checklist Implemented
JWT auth · bcrypt password hashing (pinned 4.0.1) · role-based access control · NIC/phone/email validation · file type & size validation on upload · OTP-based forgot/reset password · email verification OTP · Hold Until Approval payment protection · CORS allow-list · centralized error handling (no stack traces leaked to clients).

Refresh tokens are hashed in `auth_sessions`, rotated on every refresh, family-revoked on reuse, and revoked on logout. Production startup rejects missing/placeholder/short JWT secrets and incomplete critical configuration.

**Known limitations**: archive expansion/ZIP-bomb analysis and a full distributed Celery/ARQ queue are not implemented. The separate scheduler has retries, heartbeat, and failed-job records, but a single worker replica must be enforced by deployment until distributed leader locking is added. Camera challenge telemetry is useful anti-replay evidence but is not equivalent to a certified external liveness provider.

## Backups and restore drills

Use MongoDB Atlas continuous backups or scheduled encrypted `mongodump` archives for the database, and enable S3 versioning plus lifecycle rules for private object recovery. Keep encrypted configuration backups in a secrets manager; never include `.env` files in source-control archives. Use separate retention policies for payment/audit data and expiring chat/identity media.

At least quarterly, restore the latest backup into an isolated non-production database and bucket prefix, start the API with test-only credentials, run integrity/count checks, and execute the backend test suite. Record recovery-point and recovery-time results and alert on backup or restore-validation failures. Never test restoration by overwriting the active production database.

Tests: `pytest -q`. Docker: `docker compose up --build`. Production should require PRs, reviews,
passing workflows, secret scanning, HTTPS, private S3 buckets, restricted IAM, Redis authentication,
MongoDB authentication/TLS/backups, and monitored worker/readiness alerts.
