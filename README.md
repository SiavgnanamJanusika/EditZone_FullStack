# EditZone — Full-Stack Video Editor Marketplace

Two-sided marketplace connecting clients with professional video editors, with real-time chat,
Hold Until Approval payment protection, and admin oversight.

## Contents
- `editzone-backend/` — FastAPI + MongoDB (Motor) + Socket.IO API. See its README for setup.
- `editzone-frontend/` — React + Vite + Tailwind client. See its README for setup.

## Quick Start (local dev)

**1. Backend**
```bash
cd editzone-backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# make sure MongoDB is running (mongod) or set MONGO_URI to Atlas
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
python scripts/create_admin.py   # in a second terminal, create your admin login
```

**2. Frontend**
```bash
cd editzone-frontend
npm install
cp .env.example .env
npm run dev
```

Open http://localhost:5173 — the landing page loads first, exactly per the spec: Home / About /
Why Us / Register in the nav, Choose Role → Login/Register → role-specific dashboards.

## What's implemented end-to-end
Landing/About/Why Us → Choose Role → 2-step registration (NIC/district/gender/phone validated) →
role-based redirect (Editors page for clients, Editor Dashboard for editors) → editor search/filter
by category → project requests (pending/accepted/rejected) → real-time Socket.IO chat with file
sharing → PayHere Sandbox authorization (15%/85% commission split) → editor delivers final work
→ client approves it → backend capture releases the payment and closes the chat → client leaves a
1–5 star, 100+ character review. Admin dashboard covers users, editors, payment protection,
project payments, commission payouts, project monitoring, and reports.

## External services to configure

- PayHere Sandbox merchant and Business App credentials plus a public HTTPS notification URL
- MongoDB Atlas (application records and GridFS media storage)
- SMTP delivery for password-reset and verification OTPs

The project intentionally stays in PayHere Sandbox mode until merchant testing is complete. Payment
card and bank details are never stored in the source code; settlement uses the bank account
registered in the PayHere Merchant Account.

See each package's own README for full details, folder structure, and API/route tables.
