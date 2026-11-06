# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

KBM portal is a test prototype (MVP), not a production deployment. It bundles a shared landing page with single sign-on (`portal/`) and two existing apps: **Workplan** (attendance, leave requests, exports) and **Kniha jázd** / vehicle logbook (trips, vehicles, refuelling, monthly wizard, PDF/XLSX reports). All UI text, error messages and docs are in **Slovak**. Keep new user-facing strings in Slovak.

## Commands

Everything runs through the root Docker Compose project (`kbm-portal-demo`). Do not use the apps' original standalone Compose setups. Their READMEs describe standalone deployment and are only useful as feature docs.

```bash
sh init.sh                       # creates .env with random secrets once; never overwrites an existing one
docker compose up -d --build     # build and start; gateway on ${BIND_ADDRESS}:${PORT} (default 0.0.0.0:8090)
docker compose logs --tail=100 portal workplan-api logbook-api gateway
docker compose exec portal python reset_password.py maros
```

Never run `docker compose down -v`. It deletes the database volumes. Don't change `POSTGRES_PASSWORD` after the databases have been initialised.

Run the integration tests from the repo root with Python 3.12:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-test.txt
python -m pytest tests -q                                   # all SSO integration tests
python -m pytest tests/test_sso.py::test_employee_permissions -q   # single test
```

`tests/test_sso.py` starts the real portal and both APIs with uvicorn on ports 19100–19102. Each uses its own temporary SQLite DB. The schemas come from `Base.metadata.create_all` rather than Alembic, because Workplan's migration is PostgreSQL-only DDL.

`apps/vehicle-logbook/backend/tests/test_assistant.py` covers the monthly assistant. It stubs `portal_identity` and Gemini, so it runs from `apps/vehicle-logbook/backend` with `python -m pytest tests/test_assistant.py -q`. The PDF check runs only when `PDF_FONT`, or the DejaVu font, exists.

`apps/vehicle-logbook/backend/tests/test_logbook.py` (ledger, closings, exports, monthly drafts, vehicle deletion) uses the same stub; run it separately from `test_assistant.py`, because both share one SQLite DB per process. Workplan's backend test suite was written before SSO. It logs in through `/api/auth/login`, which now returns 410, so expect it to fail until it is updated to mock `portal_identity`.

To build the frontends, run `npm ci && npm run build` in `apps/<app>/frontend`. Each build type-checks with `tsc` first, then runs `vite build`. Vite `base` is `/workplan/` or `/vehicle-logbook/`, and the dev server proxies `/api` to `127.0.0.1:8000`.

## Architecture

```
browser ─► gateway (nginx, gateway/nginx.conf, only exposed service)
            ├─ /                      → portal:8000        (FastAPI + static landing page, SQLite in portal_data volume)
            ├─ /workplan/api/         → workplan-api:8000/api/   (FastAPI, SQLAlchemy, Alembic, Postgres 16)
            ├─ /workplan/             → workplan-ui (React/Tailwind)       [auth_request → portal /auth/app/<app>]
            ├─ /vehicle-logbook/api/  → logbook-api:8000/api/    (FastAPI, SQLAlchemy, Alembic, Postgres 17)
            ├─ /vehicle-logbook/      → logbook-ui (React/MUI)             [auth_request → portal /auth/app/<app>]
            └─ /internal/             → 404 (blocked externally)
```

**Single sign-on flow** (the main cross-cutting concern):
- `portal/main.py` is the only place that stores passwords (scrypt) and sessions, and it is the source of truth for users, per-app access (`users.apps`) and groups (`groups`, `user_groups`). Four accounts (`PEOPLE`) are seeded with `DEMO_PASSWORD` on first start only; admins add more at `/?admin=1` (`portal/static/admin.js`, `/auth/admin/*`). New users get a 72 h invite link (`invites`) to set their password, optionally e-mailed via `SMTP_*`. `migrate()` upgrades the SQLite schema in place. Sessions are 12 h, kept in the `kbm_portal_session` HttpOnly cookie. Only a SHA-256 digest of the token is stored. Changing the password deletes every session for that account.
- nginx gates each UI with `auth_request /_access/<app>` → portal `/auth/app/<app>` (401 → login, 403 → `/?denied=1`); redirects are relative (`absolute_redirect off`). Backends call `require_app()` from `portal_sso.py` and read all accounts from `/internal/directory` (`portal_directory()`, cached `PORTAL_DIRECTORY_CACHE_SECONDS`). Workplan creates missing local users and filters calendar/employees/statistics to people sharing a group (`app/directory.py`); admins see everyone. Permanent deletion (`DELETE /auth/admin/users/{slug}`) first calls each app's `/api/internal/purge-user` (`app/purge.py`, `X-SSO-Key`, via `WORKPLAN_INTERNAL_URL`/`LOGBOOK_INTERNAL_URL`; nginx returns 404 for `/<app>/api/internal/`), which deletes personal data and reassigns shared history to a "Zmazaný používateľ" placeholder, then removes the portal account.
- The API routes are **not** gated by nginx. Each backend authenticates on its own.
- Each backend's `app/portal_sso.py` (identical in both apps) forwards the cookie to `SSO_URL/internal/identity` with the `X-SSO-Key: SSO_INTERNAL_KEY` header. This happens on every protected request, through `current_user` in `workplan/backend/app/security.py` and `vehicle-logbook/backend/app/auth.py`.
- Users are mapped by **email** in Workplan and by **username** (slug) in the logbook; both are immutable in the portal. Workplan updates the local name and role from the portal identity only when they change. `app/portal_init.py` in each backend creates the four seed rows with random unusable passwords. It runs at container start after `alembic upgrade head`; see each backend's Dockerfile CMD.
- The original login endpoints in both apps are disabled and return 410.
- The portal and both APIs reject state-changing requests whose `Origin` or `Sec-Fetch-Site` is cross-site, which gives CSRF protection. The portal limits login attempts by `X-Real-IP`, a header nginx overwrites.

Logbook domain logic sits in `apps/vehicle-logbook/backend/app/`:
- `assistant.py` runs the chat-driven monthly wizard. Its draft lives in `assistant_sessions` and uploaded receipts in `assistant_files`. Chat and table edits go through the same `Operation` model, which allows edits to one concrete row only. The backend recomputes km and odometers through `recalculate`, `fill_gaps` and `distribute`, and approval writes trips and fuel purchases in one transaction (linked through `draft_id`). Gemini never writes to the logbook.
- `gemini.py` calls Gemini through `google-genai` with structured output, validated by Pydantic. The model comes from `GEMINI_MODEL` and defaults to `gemini-3.1-flash-lite`. Tests swap `gemini.client_factory` for a fake client.
- `fuel_purchases.odo` is nullable because receipts without an odometer reading stay empty. The ledger sorts such rows after the day's trips.
- `ledger.py` handles fuel and consumption balances and reports.
- `planner.py` is the deterministic monthly-trip wizard.
- `importer.py` imports the legacy `vzor_cesr.xlsm` workbook.
- `exports.py` produces PDF and XLSX output.

`EXCEL_ANALYSIS.md` documents how the original Excel formulas and VBA map onto the app.

Config lives in `.env`: `PORT`, `BIND_ADDRESS`, `COOKIE_SECURE` (set it to 1 only behind HTTPS), `POSTGRES_PASSWORD`, `SSO_INTERNAL_KEY` (also used as Workplan's `JWT_SECRET`), `DEMO_PASSWORD`, and `GEMINI_API_KEY` / `GEMINI_MODEL`. Only `logbook-api` receives the Gemini variables. Backends run with `TZ=Europe/Bratislava`.
