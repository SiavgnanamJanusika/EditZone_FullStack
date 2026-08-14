# PayHere Sandbox and Internal Escrow Audit

## Result

Project payments use PayHere Authorize (hold on card), not a provider escrow product. A verified `status_code=3` callback creates an internal funded escrow record. Client approval after delivery calls PayHere Capture and then marks the internal escrow released/payable. Actual editor bank settlement remains a separate, auditable operation; this application does not claim that PayHere pays editors directly.

## Authoritative flow

1. Both parties accept a proposal stored on the project.
2. The backend derives amount and delivery date from that proposal, creates a unique pending order, and signs the exact two-decimal amount.
3. React submits only backend-generated fields to the sandbox Authorize endpoint.
4. Only the form-encoded, signature-verified public callback can authorize the order.
5. The callback validates merchant, order, amount, currency, request context and state, then creates one `payment_escrows` record.
6. Work delivery does not release money. Only the authenticated owner can approve delivered work, with no open dispute.
7. Capture and refund use guarded locks. A release/refund collision is rejected.
8. The worker checks the immutable local ledger and, for captured/refunded/chargeback records, PayHere's Retrieval API.

## Configuration

Required backend values:

```env
PAYHERE_MERCHANT_ID=
PAYHERE_MERCHANT_SECRET=
PAYHERE_APP_ID=
PAYHERE_APP_SECRET=
PAYHERE_SANDBOX=true
PAYHERE_MODE=sandbox
PAYHERE_CURRENCY=LKR
PLATFORM_CURRENCY=LKR
PAYHERE_NOTIFY_URL=https://PUBLIC-BACKEND/api/v1/payments/payhere/notify
FRONTEND_URL=https://PUBLIC-FRONTEND
PAYHERE_RETURN_URL=https://PUBLIC-FRONTEND/payment-success
PAYHERE_CANCEL_URL=https://PUBLIC-FRONTEND/payment-failed
```

The merchant secret and App Secret are backend-only. PayHere must approve the sandbox domain/app. The notify URL must be public HTTPS; localhost callbacks cannot work. Enable the PayHere Business App permission needed for Automated Charging/Capture, Refund and Retrieval APIs.

## Safe database migration

Startup index maintenance creates `payment_escrows` with unique payment and request indexes. It backfills integer minor-unit fields without replacing legacy display fields. Existing AUTHORIZED records become FUNDED escrows and existing CAPTURED records become RELEASED/PAYABLE escrows. It does not delete or rewrite user, project or payment history.

## Manual sandbox checklist

1. Start MongoDB, backend and frontend; confirm `/docs` and `GET /api/v1/payments/sandbox/capabilities` work.
2. Expose the backend through a stable HTTPS tunnel and set that exact `PAYHERE_NOTIFY_URL` in `.env` and PayHere settings.
3. Register/log in as a client, accept an editor proposal, open its payment page and verify amount/delivery are read-only server values.
4. Complete a PayHere sandbox authorization. Verify the return page initially says pending if callback is late, then AUTHORIZED/PROTECTED.
5. In MongoDB verify one payment, one verified webhook event, one FUNDED escrow and one `authorized` ledger entry. Do not inspect or copy full authorization tokens into logs.
6. Deliver a final file as the editor. Approve as the project owner. Verify PayHere capture, CAPTURED provider status, RELEASED/PAYABLE escrow and completed project.
7. Repeat with cancel, decline, duplicate callback, altered amount/currency/custom request, refund, refund failure and chargeback simulations. Confirm no duplicate ledger/escrow and no state downgrade.
8. Run admin reconciliation and verify PayHere Retrieval results. A provider mismatch must create manual review instead of silently changing money state.

## Automated verification

```bash
cd editzone-backend && ./venv/bin/python -m compileall -q app tests && ./venv/bin/pytest -q
cd editzone-frontend && npm test -- --run && npm run build
```

Live mode remains deliberately blocked. No withdrawal feature was restored or added.
