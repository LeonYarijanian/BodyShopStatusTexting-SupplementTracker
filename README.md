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

## Hosting (Postgres, HTTPS, daily backups)

SQLite is fine on one laptop for the pitch. A real shop needs the hosted setup in `docker-compose.yml`: Postgres 16, the app (1 worker, migrations run on start), Caddy for automatic HTTPS, and a daily backup job.

1. On a server with Docker, point a DNS name (for example `app.yourdomain.com`) at it.
2. `cp .env.example .env` and set `APP_SECRET_KEY`, `POSTGRES_PASSWORD`, `DOMAIN` and, for LIVE texting, the Twilio values.
3. `docker compose up -d --build`
4. Create the shop and its first admin (the password is read from `NEW_ADMIN_PASSWORD`, or prompted):
   `docker compose exec app python -m app.seed --new-shop --name "Your Shop" --phone "(818) 555-0100" --admin-email you@yourshop.com --admin-name "Your Name"`
5. Log in at `https://$DOMAIN`, fill in Settings, and switch to LIVE only when the Mode tab shows every precondition passing.

**Backups:** the `backup` service runs `pg_dump` every day at `BACKUP_TIME_UTC` into `./backups/`, checks that each dump can be read back, and deletes dumps older than `BACKUP_KEEP_DAYS`. Copy `./backups/` off the server too (your host's volume snapshots or an `rclone` cron job), because a backup on the same disk does not survive losing the server. Take a backup by hand with `docker compose run --rm -e BACKUP_ONCE=1 backup`. Restore with:

```
docker compose exec -T db pg_restore --clean --if-exists --no-owner -U bodyshop -d bodyshop < backups/bodyshop-<stamp>.dump
```

To run the app against any Postgres without Docker, set `DATABASE_URL=postgresql+psycopg://user:password@host:5432/dbname` and run `alembic upgrade head`. To run the whole test suite against Postgres, set `TEST_POSTGRES_URL=postgresql+psycopg://user@host:5432/postgres` (each test gets its own database).

## Deleting a customer's data

When a customer asks you to delete their data, an admin opens one of their ROs and uses **Delete this customer's personal data** at the bottom of the page (type `DELETE` to confirm). This removes their name, phone, email, consent history, message text, VIN and claim numbers from every RO they have, and cancels any scheduled text. The ROs themselves stay (vehicle, amounts, stages and dates) so reports and cycle times stay correct. It cannot be undone. If the same person comes back later, they are a new customer and must give consent again.

## Daily follow-up digest

Settings > Supplements has a **Daily follow-up digest**: on business days at the send time (06:00 to 10:00), up to 5 recipients get 1 email listing every supplement whose follow-up is due, with the adjuster's contact details, the amount and how long it has waited. No email goes out when nothing is due. Emails are written to `./outbox` until `ALLOW_LIVE_EMAIL=true`, `SMTP_HOST` and `EMAIL_FROM` are set in `.env`; any SMTP provider works.

## Estimating software sync (not built yet: needs vendor access)

Section 16 item 4, pulling ROs and stages from CCC ONE, Mitchell or Audatex, cannot be built without access only the business can get:

- **CCC ONE:** third-party apps receive estimate data through the [CCC Secure Share](https://www.cccsecureshare.com/) network as CIECA BMS messages over CCC's API. The business has to apply as an app provider and is placed in an App Category that decides which estimate fields it may receive.
- **Mitchell and Audatex (Qapter):** shops export estimates as CIECA EMS or BMS files to a folder that a management system reads. Mitchell also runs partner programs for cloud integrations.
- **CIECA standards:** the EMS and BMS specifications come from CIECA membership.

Once you have Secure Share developer access or a CIECA BMS sample set from a pilot shop, the import maps onto what already exists: each estimate creates or updates an RO exactly like a CSV import row (`app/csv_import.py`), and stage changes go through `change_stage`, so texts follow the usual rules. Until then, the CSV import is the bridge.

## Photo texts

The RO page's **Send a text** box takes an optional photo (JPEG, PNG or GIF up to 5 MB), sent as a picture message with the text as its caption. Picture messages cost more per text than plain texts. Photos are stored in `./media/` (the `media` volume when hosted) under a long random name, which is the public URL Twilio fetches when sending. They are deleted 30 days after the text, and right away when the customer's data is deleted. This replaces the v1 rule of never storing photos.

## Review requests

Settings > Texting > **Review request after delivery** (off by default) sends 1 text asking for a review, at 10:00 a chosen number of days (1 to 14) after an RO is delivered. It has its own consent: the check-in form and the RO page ask separately, and repair-update consent alone is not enough. While it is on, the Delivered text leaves out the review link so repair updates stay informational. Each RO gets at most 1 request, a phone is never asked twice within 365 days, reopening the RO cancels a request that hasn't gone out, and STOP covers it. START turns repair updates back on but not review requests.

## AI-drafted adjuster emails

Each submitted supplement has a **Draft email to adjuster** button. With `ANTHROPIC_API_KEY` set, Claude (`claude-opus-5-5`, low effort, structured JSON output, server-side refusal fallback on) drafts a short, polite follow-up from that supplement's facts only: S#, claim and RO numbers, vehicle, amount, submitted date, days waiting, adjuster first name and your signature. Customer names and phone numbers are never sent to Claude. The draft is rejected and the fixed template used instead when Claude declines or errors, when the draft leaves out the exact amount, claim number or RO number, or when it uses threatening words (lawsuit, attorney, legal action and so on). Without a key, every draft uses the template.

Nothing is sent until a person clicks **Approve and send** on the draft page, after reading and editing it. Sending goes through the email settings (`./outbox` until live email is on), sets Reply-To to the sender, and logs an Email follow-up, which moves the next due date. Drafting an email costs a fraction of a cent per draft at current Claude pricing.

## Customer status page

Every RO has a status page at `PUBLIC_BASE_URL/s/<token>`, where the token is long and random. Put `{status_link}` in any stage template (Settings > Texting) to send it, or copy it from the RO page. The page shows the shop, the vehicle, the current stage, progress through the main milestones and the stage history, with the shop's phone number. It never shows prices, the insurer, claim or RO numbers, the customer's last name or phone number. It is not indexed by search engines or cached, and it stops working 30 days after pickup, when the RO is cancelled, or when the customer's data is deleted.

## Multiple locations

A shop with more than one physical location adds them in Settings > Locations. Adding the first extra location turns the shop's own phone and address into a location named Main (rename it if you like), which keeps texting from the shop's Twilio number, and every existing repair order goes there. A shop with one location works exactly as before.

- Every repair order belongs to a location: pick it on the New RO form, move it with Edit RO, or add an optional `location` column (a location name) to a CSV import. Rows without one go to the importer's current location.
- The location menu in the top bar filters the board, the supplements page, the top-bar counters and all six reports. "All locations" shows everything, with each card tagged by location. Keyword opt-outs in the texting report are per phone number, so they always count for the whole shop.
- Each user can have a home location (Settings > Users). It is what the menu starts on after login, not a permission: anyone can switch.
- Texts about an RO show its location's phone number for `{shop_phone}` and go out from the location's own Twilio number when it has one (otherwise the shop's number from the Mode tab). With a messaging service, add each location's number to the service's sender pool. Replies and STOP / START / HELP to a location's number reach the same shop, and HELP answers with that location's phone. The status page, adjuster follow-up emails and the daily digest use the RO's location, and a location can have its own review URL.
- A location with open repair orders cannot be closed. A closed location's past repair orders stay in the reports.

## Missed-call text-back and AI receptionist (separate product, not built)

Section 16 item 10 is a different product: it is about phone calls, not repair updates. It is left out of this app on purpose and would be built as its own module with its own consent rules. What it would take:

- Missed-call text-back: point the shop's Twilio number's voice webhook at the app, forward the call to the shop's real line with a ring timeout, and when nobody answers send one text to the caller ("Sorry we missed your call..."), at most once a day per number, with STOP handling. The caller never agreed to texts, so a lawyer should confirm the wording and limits first, and these texts must stay separate from the repair-update consent in this app.
- AI receptionist: a voice agent that answers when the shop can't, using speech-to-text, Claude and text-to-speech over a live call. It would answer common questions (hours, address, towing), give a repair status only after checking the caller's phone number against an open RO, book estimate appointments, and hand off to a person. That needs call recording consent, per-state disclosure rules, a calendar, and testing with real callers, none of which this app has.

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
| `POST /location` | The location menu in the top bar (Section 16 item 9) |

`POST /settings` takes a `tab` field (and an `action` field on the Locations, Insurers, Users and Mode tabs). `POST /import` takes `action=dry_run` (with the file) or `action=commit` (with the upload id).

Decisions where the spec was silent:

- The New RO form has an RO number field, and the RO page stage control has a Final invoice field used when moving to Delivered.
- `concentration_warning_pct` is edited on the Settings > Supplements tab.
- STOP / START / HELP auto-replies are sent exactly as written (no "Reply STOP" suffix) and attach to the same RO an inbound reply would.
- In reports, averages, medians and rates with no rows show `No data`; plain counts show `0`. A report with no rows at all shows `No data`.
- The default report range is today minus 89 days through today.
- Days in shop stops counting at `delivered_at` for delivered ROs.
- The demo seed's 0-day supplement is submitted at today 10:00 local, or at the current time if the seed runs before 10:00.
- Real shops are created with `python -m app.seed --new-shop` (see Hosting).
