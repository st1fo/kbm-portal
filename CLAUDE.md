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

Workplan admins can hide pages (Dochádzka/Denník/Prehľad/Žiadosti) per portal group or per person (`app/pages.py`, setting `page_access`; personal setting wins, otherwise union of groups, unconfigured group = all). `current_user` enforces it on the API via `check_request`, and `/api/auth/me` returns `pages` for the menu.

Workplan's monthly hours sheet (`app/hours.py`, `GET /api/hours`, `/api/hours/export` XLSX; frontend `HoursReport.tsx` on Prehľad) is computed only from the attendance plan, not from Denník pípnutia: fund = working days × the user's `daily_hours`; a work-category day (`common.is_work`) counts the full úväzok minus approved partial-day absences; a full-day absence counts as hours of that type; a past working day without an entry counts as worked but is flagged. Partial-day absences are leave requests with `hours` (one day only, migration 0005); on approval they do not replace the day's plan. Admins see everyone (team filter), other users only themselves.

Workplan's Denník has kanban boards (`app/boards.py`, migration 0004 moved the old `todos` into a personal board „Moje úlohy“; frontend `journal/Board*.tsx` with `@dnd-kit`). A board without members is personal; members see and edit cards and columns, only the owner (or an admin) changes settings, members or deletes it. Cards are deliberately not linked to Denník tasks/time; „Zapísať ako aktivitu“ only prefills the activity form.

Denník also has an AI day fill (`app/journal_ai.py`, `app/gemini.py`, frontend `journal/AiDayDialog.tsx`): free text → Gemini proposes activities/new tasks inside the day's unlogged worked time; the backend checks overlaps and pípnutia, and only `POST /api/journal/ai/apply` writes (one transaction). Tests replace `gemini.propose`.

On phones the logbook (≤700 px) shows the data pages (`NAV` items with `context`) in a fixed bottom bar (`.bottom-nav`) and the remaining pages, theme and logout in the avatar menu; the vehicle/month bar is one row; columns marked `.wide-only` are hidden. Workplan (≤640 px) turns its `.topbar nav` into the same kind of fixed bottom bar purely in CSS (`.nav-short` gives „Správa“ for Administrácia).

Both apps have a first-run tour (`Tour.tsx` in each frontend, same logic): steps highlight `[data-tour=…]` elements and may switch the page; it opens once per user (`localStorage` `kbm-workplan-tour-done:<id>` / `kbm-logbook-tour-done:<username>`) and again from the „?“ button (on phones in the logbook from the avatar menu). A missing target skips the step.

All three UIs share a light/dark theme: `localStorage['kbm-theme']` (`light`/`dark`/`auto`) → `data-theme` on `<html>`, applied before paint by a small non-inline script (CSP forbids inline) and switched from the top bar. Use the CSS tokens on `:root` in `apps/workplan/frontend/src/styles.css` instead of hex colours.

Logbook domain logic sits in `apps/vehicle-logbook/backend/app/`:
- `assistant.py` runs the chat-driven monthly wizard. Its draft lives in `assistant_sessions` and uploaded receipts in `assistant_files`. Chat and table edits go through the same `Operation` model, which allows edits to one concrete row only. The backend recomputes km and odometers through `recalculate`, `fill_gaps` and `distribute`, and approval writes trips and fuel purchases in one transaction (linked through `draft_id`). Gemini never writes to the logbook.
- `gemini.py` calls Gemini through `google-genai` with structured output, validated by Pydantic. The model comes from `GEMINI_MODEL` and defaults to `gemini-3.6-flash`; on 429/5xx one retry goes to `GEMINI_FALLBACK_MODEL` (default `gemini-3.1-flash-lite`). Both apps share these variables. Tests swap `gemini.client_factory` for a fake client.
- `fuel_invoice.py` imports FleetCor (Shell Card) summary invoices (admin only, `POST /api/fuel-invoices`, preview then `apply` with the PDF digest; frontend `FuelInvoice.tsx` on Tankovanie). Text comes from pdfplumber (`x_tolerance=1`), no AI; per-card and grand totals must match to the cent. Rows are matched to vehicles by normalised ŠPZ; every skipped row carries a reason (unknown vehicle, duplicate, closed period, over capacity, non-fuel product). A repeated upload is decided per row (voucher in the note or same day/litres/amount = duplicate), so rows skipped earlier (e.g. the vehicle was added later) can still be written; `fuel_invoices` only records what was imported. Each fuel row's note is `FleetCor <invoice>, poukážka <n>`.
- The monthly assistant loads the month's rows already in the logbook (`saved_rows`): fuel purchases (e.g. from a FleetCor invoice) count in the tank and are sent to Gemini read-only (`context()["v_knihe"]`); manually entered trips stay as fixed odometer spans (`kept_spans`, segments with `fixed=True`) and the draft plans only the kilometres between them. Earlier wizard estimates still need „Nahradiť uložený odhad“. The opening odometer is prefilled from the logbook. The closing reading may carry a date (`end_day`): inside the month trips are planned only up to it; after the month `closing()` estimates the state on the last day by working days. There is no receipt-confirm step: the schedule is created automatically (`auto_plan`) once start, end and routes are known, and approval requires `receipts_checked` when the draft has receipts. Planning helpers take a `Frame` (anchors, kept trips, closing) from `check_month`; per-request data (month rows, holidays, history, opening tank level) is memoised in `db.info` via `cached()`. Refuels without an odometer (FleetCor, most receipts) do not split kilometres: trip km follow the destinations (`distribute` within hard odometer segments), then `schedule_for_fuel` picks the days of estimated trips around those refuels (subset sum per refuel window) so the tank by the norm neither runs dry before a refuel nor overflows; `build_balanced` adds a trip to the longest destination only while that reduces tank problems. If the month's fuel cannot cover the km, only the end is negative and `tank_balance` explains it. Destination lengths (`known_km`) come first from the vehicle's own recorded trips from the same origin (`history_km`), then the catalogue (history and catalogue values under 20 km outside the city are ignored), then the Žilina table. When the model invents destinations (`routes_invented`) or the history fallback is used, `fit_counts` adjusts how many times each is driven to match the kilometres. The chat reply is filtered by `honest_reply` (drops sentences where the model claims it changed the plan). The frontend shows the whole month day by day (`MonthlyWizard.tsx`, `.month-days`), edits rows in a dialog.
- `fuel_purchases.odo` is nullable because receipts without an odometer reading stay empty. The ledger sorts such rows after the day's trips.
- `ledger.py` handles fuel and consumption balances and reports.
- `planner.py` is the deterministic monthly-trip wizard.
- `importer.py` imports the legacy `vzor_cesr.xlsm` workbook.
- `exports.py` produces PDF and XLSX output.

`EXCEL_ANALYSIS.md` documents how the original Excel formulas and VBA map onto the app.

Config lives in `.env`: `PORT`, `BIND_ADDRESS`, `COOKIE_SECURE` (set it to 1 only behind HTTPS), `POSTGRES_PASSWORD`, `SSO_INTERNAL_KEY` (also used as Workplan's `JWT_SECRET`), `DEMO_PASSWORD`, and `GEMINI_API_KEY` / `GEMINI_MODEL`. `logbook-api` and `workplan-api` receive the Gemini variables. Backends run with `TZ=Europe/Bratislava`.
