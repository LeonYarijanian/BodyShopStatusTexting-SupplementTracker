# Body Shop Status Texting + Supplement Tracker

A web app for a small collision repair shop. It texts customers automatic repair updates and tracks every insurance supplement until the insurer decides.

`SPEC.md` is the full build spec. This app follows it exactly.

## Setup

Requires Python 3.12.

| Purpose | Command |
| --- | --- |
| Create virtual environment (Mac/Linux) | `python3.12 -m venv .venv && source .venv/bin/activate` |
| Create virtual environment (Windows) | `py -3.12 -m venv .venv` then `.venv\Scripts\activate` |
| Install | `pip install -r requirements.txt` |
| Configure | `cp .env.example .env`, then set `APP_SECRET_KEY` (64 random hex characters) |
| Create or upgrade the database | `alembic upgrade head` |
| Load demo data | `python -m app.seed --demo` |
| Run the app | `uvicorn app.main:app --port 8000` |
| Run the tests | `pytest -q` |

Generate a secret key with `python -c "import secrets; print(secrets.token_hex(32))"`.

Run uvicorn with 1 worker only, so the scheduler runs once.

## Modes

The app starts in **DEMO** mode: texts are saved to the database and printed to the console, and nothing leaves the computer. **LIVE** mode sends real texts through Twilio and is allowed only when all 6 preconditions in SPEC.md Section 7 are true.

## Pitch demo

After `alembic upgrade head` and `python -m app.seed --demo`, log in as `admin@demo.local` with password `demo-password-123` and follow the 7-step walkthrough in SPEC.md Section 14. Everything works offline: HTMX and Pico.css are vendored in `app/static/`.

## Tests

`pytest -q` runs every acceptance test in SPEC.md Section 15 (T0.1 to T7.3), one file per phase in `tests/`, plus a few smoke tests and an automated run of the pitch walkthrough. Tests freeze the clock with freezegun, give each test a fresh SQLite database, and never call the real Twilio API.

## Notes on the build

Routes the spec needs but does not name:

| Route | Purpose |
| --- | --- |
| `POST /logout` | Log out (top bar) |
| `GET/POST /ro/{id}/edit` | Edit RO (the New RO form without the consent block) |
| `GET /adjusters/options`, `POST /adjusters` | Adjuster dropdown filtered by insurer, and the inline Add adjuster |
| `POST /supplements/{id}/edit` | Edit a DRAFT supplement |
| `POST /settings/preview` | Live template preview on the Texting tab |

`POST /settings` takes a `tab` field (and an `action` field on the Insurers, Users and Mode tabs). `POST /import` takes `action=dry_run` (with the file) or `action=commit` (with the upload id).

Decisions where the spec was silent:

- The New RO form has an RO number field, and the RO page stage control has a Final invoice field used when moving to Delivered.
- `concentration_warning_pct` is edited on the Settings > Supplements tab.
- STOP / START / HELP auto-replies are sent exactly as written (no "Reply STOP" suffix) and attach to the same RO an inbound reply would.
- In reports, averages, medians and rates with no rows show `No data`; plain counts show `0`. A report with no rows at all shows `No data`.
- The default report range is today minus 89 days through today.
- Days in shop stops counting at `delivered_at` for delivered ROs.
- The demo seed's 0-day supplement is submitted at today 10:00 local, or at the current time if the seed runs before 10:00.
- There is no way to create a real (non-demo) shop yet; v1 is the offline pitch build.
