# ENIGMA frontend

Next.js App Router UI built with React, TypeScript, and Tailwind CSS. It uses the existing FastAPI API and scopes property requests with the selected membership's `X-Property-ID` header.

## Run locally

1. Start the backend from the repository root:

   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
   ```

2. In this directory, copy `.env.example` to `.env.local` if the backend is not at the default address. Set `ENIGMA_API_BASE_URL` to the FastAPI origin.

3. Install frontend dependencies and start Next.js:

   ```powershell
   npm install
   npm run dev
   ```

4. Open `http://localhost:3000`. Administrators can sign in with their bootstrapped email and password. Resident and owner accounts use the customer portals.

The Next.js route handler at `/api/backend/*` proxies requests to FastAPI so the browser uses a same-origin URL and does not require a backend CORS change. The bearer token is held in session storage and cleared when the user signs out or the API rejects the session.

## Google sign-in and customer portals

Set `ENIGMA_GOOGLE_CLIENT_ID`, `ENIGMA_GOOGLE_CLIENT_SECRET`, and `ENIGMA_GOOGLE_REDIRECT_URI` in the backend `.env`. Register the exact callback URI `http://localhost:3000/api/backend/api/v1/auth/google/callback` with the Google OAuth client for local development, and set `ENIGMA_FRONTEND_BASE_URL=http://localhost:3000`. New accounts use Google sign-up, verify the Google identity, and set an ENIGMA password before proceeding. Configure SMTP in the backend environment for forgot-password emails.

Resident-only accounts open `/tenant` by default. Tenant records are linked only when the verified Google email matches a unique resident record. The tenant dashboard shows linked tenancy and charges and allows maintenance requests for active leased units. Use the **Owner** portal switch to start the first property listing; owners can save drafts, upload photographs/floor plans/videos, and submit listings for review. Owner listing data and media storage paths can be configured with `ENIGMA_OWNER_LISTINGS_DB` and `ENIGMA_LISTING_MEDIA_DIR`.

## Implemented screens

- Sign-in, property membership selection, active property context, and permission-aware navigation.
- Property dashboard and combined review queue.
- Document PDF analysis and extraction result review.
- Invoices, payments, expenses, CSV bank statement import, reconciliation decisions, and financial reports.
- Maintenance requests, triage, work-order conversion and auto-dispatch, SLA analysis, inspections, assets, and preventive schedules.
- Resident, lease, unit, and vendor records; read-only resident/lease analysis.
- Compliance findings, approval decisions, workflow monitoring, notifications, audit verification, and property settings.

The frontend does not replace API authorization. Unsupported backend capabilities are identified in the relevant screen: durable document correction and storage, detailed saved-document workflow inputs, explicit vendor selection, inspection history, user administration, and non-email notifications.
