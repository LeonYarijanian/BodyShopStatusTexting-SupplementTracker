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
