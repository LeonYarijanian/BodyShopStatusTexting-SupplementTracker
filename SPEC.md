# Body Shop Status Texting + Supplement Tracker: Claude Code Build Spec

Oct 1, 2026 · @Leon Yarijanian

## 1. How to use this spec

Build this app one phase at a time with Claude Code. Do not start a phase until every acceptance test in the previous phase passes. Save this document as `SPEC.md` in an empty project folder, open Claude Code in that folder, and send:

> Read SPEC.md fully. Build Phase 0 only. Stop when its tests pass.

For each later phase, send the same message with the next phase number. Phases and their tests are in Section 15.

### Hard rules for the coding agent

1. Build exactly what this spec says. Do not add features, pages, fields, settings or libraries that are not written here.
2. Use the exact names in this spec for tables, fields, enums, routes, files and settings. Do not rename anything.
3. Use only the libraries listed in Section 4. If another library seems required, stop and ask first.
4. Write one automated test for every acceptance test in the current phase. A phase is done only when `pytest -q` reports 0 failures.
5. Never send a real text message unless every LIVE mode precondition in Section 7 is true. Otherwise use the DEMO provider, which only writes to the database and the console.
6. Never commit secrets. Keys live in `.env`, and `.env` is listed in `.gitignore`.
7. Store every timestamp in UTC. Convert to the shop's time zone (default `America/Los_Angeles`) only for display, quiet hours and business-day math.
8. Store money as integer cents. $1,234.56 is stored as `123456`. Never use floats for money.
9. Round displayed decimals with `decimal.ROUND_HALF_UP`. Never use Python's built-in `round()`.
10. After each phase, stop and print three things: the files created or changed, the test count and result, and the exact command to run the app.
11. If two parts of this spec conflict, or something is ambiguous, stop and ask. Do not guess.

## 2. Product summary

The app lets a small collision repair shop do two things: text customers automatic repair updates, and track every insurance supplement until the insurer decides. It is a web app that shop staff use on a desktop or tablet. Customers never log in. They only receive texts.

| Module | What it does | Who uses it | Why the shop pays |
| --- | --- | --- | --- |
| Status Texting | Sends the customer one automatic text when staff move the car to certain repair stages | Front office, estimators | Fewer "is my car ready?" calls, happier customers |
| Supplement Tracker | Lists every open supplement, counts business days waited, and reminds staff to chase slow adjusters | Estimators, shop manager | Cars spend fewer days stuck in bays, so the shop finishes more cars per month |

Reports (Section 10) sit on top of both modules: cycle time, supplement approval speed by insurer and adjuster, revenue share by payer, and days cars sat waiting on supplements.

**Target customer for v1:** an independent shop with 1 location, 2 to 10 staff, and roughly 20 to 120 repair orders per month. Staff enter repair orders by hand or by CSV import (Section 11). The database supports many shops from day one (every table carries `shop_id`), but each user belongs to exactly 1 shop.

**Not in v1:** customer login or portal, AI-written replies to customers, outbound phone calls, payments, parts ordering, photos, integration with any estimating or shop-management software (CCC ONE, Mitchell, Audatex), multiple locations per shop, email sending, and mobile apps. Section 16 lists what may come later.

## 3. Definitions

Every term below means exactly this everywhere in the spec and the code.

| Term | Meaning |
| --- | --- |
| Body shop / collision shop | A business that repairs vehicle body damage, usually after an accident. One shop = one `shops` row. |
| Repair order (RO) | One repair job for one vehicle, from check-in to delivery. Identified by `ro_number`, unique within a shop. |
| Customer | The person who receives texts about the RO. Identified by phone number within a shop. |
| Stage | The current step of the repair. Exactly 1 stage per RO at any moment. The full list is in Section 6. |
| Teardown | Taking the damaged area apart to find hidden damage. |
| Estimate | The shop's written list of repairs and prices. `original_estimate_cents` is the first one. |
| Insurer | The insurance company paying for the repair. |
| Adjuster | The insurer's employee who reviews and approves estimates and supplements. Belongs to 1 insurer. |
| Claim number | The insurer's ID for the accident claim. Free text. |
| Supplement | An add-on estimate written after teardown for damage not on the original estimate. The insurer must approve it before paying for that work. |
| Requested amount | The dollar total of a supplement as submitted, stored in cents. |
| Approved amount | The dollar total the insurer approved, stored in cents. 0 until a decision. |
| Payer type | Who pays for the RO: `INSURANCE` or `CUSTOMER_PAY`. |
| Customer-pay | The customer pays the shop directly. No insurer is involved. |
| DRP (direct repair program) | An insurer sends customers to this shop and grades the shop on numbers like cycle time. Stored as a yes/no flag per insurer. |
| Cycle time (keys-to-keys) | Calendar days from `checked_in_at` to `delivered_at`, as a decimal rounded to 1 place. |
| Business day | Monday through Friday in the shop's time zone. Saturday and Sunday are not business days. Holidays are ignored in v1. |
| Days open | Business days a supplement has waited for a decision. Formula in Section 8. |
| Aging bucket | A group of open supplements by days open: FRESH, WATCH, LATE, CRITICAL (Section 8). |
| Follow-up | A logged staff action contacting the adjuster about a waiting supplement. |
| Consent | The customer's permission to receive repair texts, recorded with time, method and staff member. |
| Opt-out | The customer replied STOP or a synonym (Section 7). No more texts go to that phone from that shop until they reply START. |
| Quiet hours | Local times when no text may be sent. Default: from 20:00 to 08:00. |
| Cool-off window | Minutes the app waits after a stage change before texting, so a mistaken click can be corrected. Default: 10. |
| Daily cap | Maximum stage-update texts sent to one RO's customer per local calendar day. Default: 3. |
| DEMO mode | Texts are saved to the database and printed to the console. Nothing leaves the computer. The default. |
| LIVE mode | Texts go to real phones through Twilio. Allowed only when all LIVE preconditions in Section 7 are true. |
| Twilio | The SMS provider used in LIVE mode. |
| A2P 10DLC | The US carrier registration a business must complete before texting customers from a 10-digit number. Done in the Twilio console, outside this app. |
| E.164 | Phone format with `+` and country code, for example `+18185550123`. Every phone number is stored this way. |
| Tenant | One shop's data. Every database query filters by the logged-in user's `shop_id`. |

## 4. Tech stack and folder structure

The app is Python 3.12 with FastAPI, server-rendered HTML and a single SQLite file. Pin the exact current version of every library in `requirements.txt` during Phase 0, and never upgrade them silently.

| Layer | Use exactly | Notes |
| --- | --- | --- |
| Language | Python 3.12 | |
| Web framework | `fastapi` + `uvicorn[standard]` | Run with 1 worker only, so the scheduler runs once |
| Forms | `python-multipart` | Needed for HTML form posts |
| HTML | `jinja2` templates | Server-rendered pages, no React |
| Interactivity | HTMX, vendored to `app/static/htmx.min.js` | No CDN, so the demo works offline |
| CSS | Pico.css, vendored to `app/static/pico.min.css`, plus `app/static/app.css` | |
| Database | SQLite through `sqlalchemy` 2.x ORM | File: `./bodyshop.db` |
| Migrations | `alembic` | One migration per phase that changes tables |
| Settings | `pydantic-settings` (reads `.env`) | |
| Sessions | Starlette `SessionMiddleware` with `itsdangerous` | Signed cookie |
| Passwords | `argon2-cffi` (argon2id) | |
| Scheduler | `apscheduler` BackgroundScheduler | One job, runs every 60 seconds |
| Phone numbers | `phonenumbers` | Parse US numbers to E.164 |
| Time zones | `zoneinfo` (standard library) | |
| SMS in LIVE mode | `twilio` | Never imported by tests except through a mock |
| Tests | `pytest`, `httpx`, `freezegun` | `freezegun` freezes the clock for time tests |

This is the full approved list. Anything else needs approval first (rule 3 in Section 1).

### Folder structure

```
bodyshop-tracker/
  app/
    main.py              # FastAPI app, route registration, scheduler start
    config.py            # Settings class reading .env (Section 13)
    db.py                # engine and session factory
    models.py            # SQLAlchemy models (Section 5)
    enums.py             # every enum (Sections 5 to 8)
    auth.py              # login, sessions, password hashing, role checks
    business_days.py     # business-day math (Section 8)
    money.py             # dollars-string <-> cents, display formatting
    messaging/
      engine.py          # consent, quiet hours, cool-off, daily cap (Section 7)
      templates.py       # stage templates and rendering (Section 6)
      providers.py       # DemoProvider and TwilioProvider
      inbound.py         # STOP / START / HELP keywords, inbound replies
    supplements.py       # transitions, aging, follow-ups (Section 8)
    reports.py           # report formulas (Section 10)
    csv_import.py        # CSV import (Section 11)
    scheduler.py         # the 60-second job
    seed.py              # demo data (Section 14)
    routes/
      auth_routes.py
      board.py
      repair_orders.py
      supplements_routes.py
      reports_routes.py
      settings_routes.py
      import_routes.py
      messages_routes.py
      webhooks.py        # Twilio inbound and status callbacks (LIVE only)
    templates/           # Jinja2 HTML
    static/              # pico.min.css, htmx.min.js, app.css
  migrations/            # Alembic
  tests/
    fixtures.py          # deterministic fixture data (Section 15)
  .env.example
  .gitignore
  requirements.txt
  README.md
  SPEC.md
```

### Commands

| Purpose | Command |
| --- | --- |
| Create virtual environment (Mac/Linux) | `python3.12 -m venv .venv && source .venv/bin/activate` |
| Create virtual environment (Windows) | `py -3.12 -m venv .venv` then `.venv\Scripts\activate` |
| Install | `pip install -r requirements.txt` |
| Create or upgrade the database | `alembic upgrade head` |
| Load demo data | `python -m app.seed --demo` |
| Run the app | `uvicorn app.main:app --port 8000` |
| Run the tests | `pytest -q` |

## 5. Data model

The database has exactly 13 tables, listed below. Every table has `id` (integer primary key), `created_at` and `updated_at` (UTC). Every table except `shops` and `login_attempts` has `shop_id` (foreign key to `shops.id`, required, indexed). Store enums as strings (`native_enum=False`) so SQLite works.

### Enums (`app/enums.py`)

| Enum | Values, in this order |
| --- | --- |
| `Role` | `ADMIN`, `STAFF` |
| `PayerType` | `INSURANCE`, `CUSTOMER_PAY` |
| `Stage` | The 14 codes in Section 6, in Section 6's order |
| `ConsentStatus` | `OPTED_IN`, `OPTED_OUT` |
| `ConsentMethod` | `IN_PERSON_VERBAL`, `SIGNED_FORM`, `KEYWORD`, `IMPORTED` |
| `MessageDirection` | `OUTBOUND`, `INBOUND` |
| `MessageKind` | `STAGE_UPDATE`, `MANUAL`, `OPT_OUT_CONFIRMATION`, `OPT_IN_CONFIRMATION`, `HELP_REPLY`, `INBOUND_REPLY`, `INBOUND_KEYWORD` |
| `MessageStatus` | `SCHEDULED`, `SENT`, `DELIVERED`, `FAILED`, `CANCELLED_SUPERSEDED`, `BLOCKED_NO_CONSENT`, `BLOCKED_OPTED_OUT`, `RECEIVED` |
| `MessagingMode` | `DEMO`, `LIVE` |
| `SupplementStatus` | `DRAFT`, `SUBMITTED`, `APPROVED`, `PARTIALLY_APPROVED`, `DENIED`, `WITHDRAWN` |
| `SupplementEventType` | `STATUS_CHANGE`, `FOLLOW_UP`, `NOTE` |
| `FollowUpMethod` | `PHONE`, `EMAIL`, `INSURER_PORTAL`, `OTHER` |
| `AgingBucket` | `FRESH`, `WATCH`, `LATE`, `CRITICAL` |

### `shops`

| Field | Type | Required | Rules |
| --- | --- | --- | --- |
| `name` | string(120) | yes | Shown in every text |
| `phone_e164` | string(16) | yes | Shop phone, shown in HELP replies |
| `timezone` | string(64) | yes | Default `America/Los_Angeles`; must be a valid IANA name |
| `address` | string(200) | no | |

### `shop_settings`

Exactly 1 row per shop (`shop_id` unique). Fields, defaults and allowed ranges are in Section 13.

### `users`

| Field | Type | Required | Rules |
| --- | --- | --- | --- |
| `email` | string(254) | yes | Stored lowercase; unique across all shops |
| `password_hash` | string | yes | argon2id hash; never store the password |
| `full_name` | string(120) | yes | |
| `role` | `Role` | yes | |
| `is_active` | boolean | yes | Default true; inactive users cannot log in |

### `login_attempts`

| Field | Type | Required | Rules |
| --- | --- | --- | --- |
| `email` | string(254) | yes | Lowercase, as typed |
| `attempted_at` | datetime UTC | yes | |
| `succeeded` | boolean | yes | |

### `customers`

| Field | Type | Required | Rules |
| --- | --- | --- | --- |
| `first_name` | string(60) | yes | Used in texts |
| `last_name` | string(60) | no | |
| `phone_e164` | string(16) | yes | Unique per shop: (`shop_id`, `phone_e164`) |
| `email` | string(254) | no | |

### `insurers`

| Field | Type | Required | Rules |
| --- | --- | --- | --- |
| `name` | string(120) | yes | Unique per shop, case-insensitive |
| `is_drp` | boolean | yes | Default false |
| `follow_up_interval_business_days` | integer | no | 1 to 10; null means use the shop default |
| `claims_email` | string(254) | no | |
| `claims_phone_e164` | string(16) | no | |

### `adjusters`

| Field | Type | Required | Rules |
| --- | --- | --- | --- |
| `insurer_id` | FK to `insurers` | yes | |
| `full_name` | string(120) | yes | First word is used as first name in emails |
| `email` | string(254) | no | |
| `phone_e164` | string(16) | no | |

### `repair_orders`

| Field | Type | Required | Rules |
| --- | --- | --- | --- |
| `ro_number` | string(20) | yes | Unique per shop |
| `customer_id` | FK to `customers` | yes | |
| `vehicle_year` | integer | yes | 1950 to (current year + 1) |
| `vehicle_make` | string(40) | yes | Example: `Honda` |
| `vehicle_model` | string(60) | yes | Example: `Accord` |
| `vehicle_color` | string(30) | no | |
| `vin` | string(17) | no | If present: exactly 17 characters, A to Z and 0 to 9, no I, O or Q; stored uppercase |
| `payer_type` | `PayerType` | yes | |
| `insurer_id` | FK to `insurers` | if `INSURANCE` | Must be null if `CUSTOMER_PAY` |
| `adjuster_id` | FK to `adjusters` | no | Must belong to `insurer_id`; null if `CUSTOMER_PAY` |
| `claim_number` | string(40) | no | Null if `CUSTOMER_PAY` |
| `original_estimate_cents` | integer | yes | 0 or more |
| `final_invoice_cents` | integer | when delivered | Required before moving to `DELIVERED` |
| `current_stage` | `Stage` | yes | |
| `checked_in_at` | datetime UTC | yes | Not in the future |
| `delivered_at` | datetime UTC | no | Set automatically on `DELIVERED`; must be on or after `checked_in_at` |
| `needs_reply` | boolean | yes | Default false; set true by an inbound non-keyword text |

### `stage_events`

| Field | Type | Required | Rules |
| --- | --- | --- | --- |
| `repair_order_id` | FK | yes | |
| `from_stage` | `Stage` | no | Null for the first event |
| `to_stage` | `Stage` | yes | |
| `changed_by_user_id` | FK to `users` | yes | |
| `changed_at` | datetime UTC | yes | |
| `note` | string(200) | no | `imported` for CSV rows |

### `consents`

One row per change, never updated. The current consent for a phone is the row with the latest `recorded_at` for (`shop_id`, `phone_e164`); ties go to the highest `id`. No row means no consent.

| Field | Type | Required | Rules |
| --- | --- | --- | --- |
| `customer_id` | FK | yes | |
| `phone_e164` | string(16) | yes | |
| `status` | `ConsentStatus` | yes | |
| `method` | `ConsentMethod` | yes | |
| `recorded_by_user_id` | FK to `users` | no | Null when `method` is `KEYWORD` |
| `recorded_at` | datetime UTC | yes | |

### `messages`

| Field | Type | Required | Rules |
| --- | --- | --- | --- |
| `repair_order_id` | FK | no | Null only for an inbound text that matches no RO |
| `customer_id` | FK | no | |
| `direction` | `MessageDirection` | yes | |
| `kind` | `MessageKind` | yes | |
| `status` | `MessageStatus` | yes | |
| `to_e164` | string(16) | yes | |
| `from_e164` | string(16) | yes | Shop phone or Twilio number |
| `body` | text | yes | Rendered text, as sent or received |
| `stage` | `Stage` | no | The stage that triggered a `STAGE_UPDATE` |
| `scheduled_send_at` | datetime UTC | no | |
| `sent_at` | datetime UTC | no | |
| `provider_message_id` | string(64) | no | Twilio SID, or `demo-` + UUID4 in DEMO mode |
| `error_text` | string(200) | no | Example: `BODY_TOO_LONG` |
| `created_by_user_id` | FK | no | Set for `MANUAL` texts |

### `supplements`

| Field | Type | Required | Rules |
| --- | --- | --- | --- |
| `repair_order_id` | FK | yes | The RO's `payer_type` must be `INSURANCE` |
| `sequence_number` | integer | yes | 1, 2, 3 within the RO; unique per RO; shown as S1, S2, S3 |
| `status` | `SupplementStatus` | yes | Starts at `DRAFT` |
| `description` | string(500) | yes | Example: `Hidden damage: RF apron, headlamp bracket` |
| `requested_cents` | integer | yes | Greater than 0 |
| `approved_cents` | integer | yes | Default 0; never more than `requested_cents` |
| `adjuster_id` | FK | no | Defaults to the RO's adjuster |
| `submitted_at` | datetime UTC | no | Set on `SUBMITTED` |
| `decided_at` | datetime UTC | no | Set on `APPROVED`, `PARTIALLY_APPROVED` or `DENIED` |
| `next_follow_up_due_at` | datetime UTC | no | Section 8 |
| `follow_up_count` | integer | yes | Default 0 |
| `last_follow_up_at` | datetime UTC | no | |

### `supplement_events`

| Field | Type | Required | Rules |
| --- | --- | --- | --- |
| `supplement_id` | FK | yes | |
| `event_type` | `SupplementEventType` | yes | |
| `from_status` | `SupplementStatus` | no | For `STATUS_CHANGE` |
| `to_status` | `SupplementStatus` | no | For `STATUS_CHANGE` |
| `follow_up_method` | `FollowUpMethod` | no | Required for `FOLLOW_UP` |
| `note` | string(500) | no | |
| `user_id` | FK to `users` | yes | |
| `occurred_at` | datetime UTC | yes | |

**Indexes:** `messages` (`status`, `scheduled_send_at`); `supplements` (`shop_id`, `status`); `repair_orders` (`shop_id`, `current_stage`); `consents` (`shop_id`, `phone_e164`, `recorded_at`).

## 6. Repair stages and text templates

There are 14 stages, and 8 of them text the customer by default. Each texting stage fires at most once per RO. Admins can turn any stage's text on or off and edit its template in Settings, except `CANCELLED`, which never texts.

| Order | Code | Staff label | Texts by default | Default template |
| --- | --- | --- | --- | --- |
| 1 | `CHECKED_IN` | Checked in | Yes | `Hi {first_name}, this is {shop_name}. Your {vehicle} is checked in (RO {ro_number}). We'll text you as the repair moves along. Reply STOP to opt out, HELP for help.` |
| 2 | `WAITING_ON_INSURANCE` | Waiting on insurance | Yes | `{shop_name}: We sent the estimate for your {vehicle} to your insurance company and are waiting for their approval. We'll update you when we hear back.` |
| 3 | `TEARDOWN` | Teardown | No | `{shop_name}: We're taking apart the damaged area of your {vehicle} to check for hidden damage.` |
| 4 | `SUPPLEMENT_PENDING` | Supplement pending | Yes | `{shop_name}: We found more damage on your {vehicle} and asked your insurance company to approve the extra repairs. We'll keep you posted.` |
| 5 | `PARTS_ORDERED` | Parts ordered | Yes | `{shop_name}: Parts for your {vehicle} are ordered. We'll start repairs as soon as they arrive.` |
| 6 | `PARTS_RECEIVED` | Parts received | No | `{shop_name}: Parts for your {vehicle} have arrived.` |
| 7 | `BODY_REPAIR` | Body repair | Yes | `{shop_name}: Repairs on your {vehicle} have started.` |
| 8 | `PAINT` | Paint | Yes | `{shop_name}: Your {vehicle} is in the paint department.` |
| 9 | `REASSEMBLY` | Reassembly | No | `{shop_name}: Your {vehicle} is being put back together.` |
| 10 | `QUALITY_CHECK` | Quality check | No | `{shop_name}: Your {vehicle} is in final quality check.` |
| 11 | `READY_FOR_PICKUP` | Ready for pickup | Yes | `{shop_name}: Your {vehicle} is ready for pickup! Call {shop_phone} with any questions.` |
| 12 | `DELIVERED` | Delivered | Yes | `{shop_name}: Thanks for trusting us with your {vehicle}.{review_link_sentence}` |
| 13 | `ON_HOLD` | On hold | No | `{shop_name}: The repair on your {vehicle} is on hold. We'll call you with details.` |
| 14 | `CANCELLED` | Cancelled | Never | None |

Active stages are every stage except `DELIVERED` and `CANCELLED`. Those two are terminal stages.

### Template variables

| Variable | Replaced with | Example |
| --- | --- | --- |
| `{first_name}` | `customers.first_name` | `Maria` |
| `{shop_name}` | `shops.name` | `Brand Blvd Collision (Demo)` |
| `{shop_phone}` | `shops.phone_e164` formatted as US national | `(818) 555-0100` |
| `{vehicle}` | `vehicle_year` + space + `vehicle_make` + space + `vehicle_model` | `2021 Honda Accord` |
| `{ro_number}` | `repair_orders.ro_number` | `24-1187` |
| `{review_link_sentence}` | If `review_url` is set: a space + `If you have a minute, a review helps us a lot: ` + the URL. Otherwise an empty string. | `If you have a minute, a review helps us a lot: https://g.page/r/example` |

### Rendering rules, applied in this order

1. Replace every variable exactly. A template that contains any other `{...}` variable is rejected when saved, with the error `Unknown variable {name}`.
2. Identification: if the rendered text does not contain `shops.name`, add `{shop_name}:` to the front.
3. First-message rule: if this shop has never had an `OUTBOUND` message with status `SENT` or `DELIVERED` to this phone number, and the text does not contain `STOP`, add `Reply STOP to opt out.` to the end.
4. Length: if the final text is longer than 320 characters, do not send. Set the message to `FAILED` with `error_text` = `BODY_TOO_LONG`.
5. A raw template longer than 250 characters is rejected when saved.
6. Templates must stay informational. No discounts, offers or ads. This rule is for the people editing templates; the app does not check it.

### Stage change rules

1. Staff may move an RO from any active stage to any other stage, forward or backward, because real repairs jump around.
2. Moving to the stage the RO is already in does nothing and creates no event.
3. Moving to `DELIVERED` requires `final_invoice_cents`. It sets `delivered_at` to now.
4. Moving out of `DELIVERED` or `CANCELLED` requires the `ADMIN` role. It sets `delivered_at` back to null.
5. Every change inserts 1 `stage_events` row.
6. A stage text is created only if both are true: (a) the stage's text is turned on in Settings, and (b) the RO has no earlier `stage_events` row with `to_stage` equal to this stage. Then Section 7 takes over.
7. Supplements and stages are independent in v1. Changing one never changes the other automatically.

## 7. Messaging engine rules

No text is ever sent without consent, during quiet hours, or over the daily cap, and DEMO mode never sends anything off the computer. "Sent successfully" in this spec means status `SENT` or `DELIVERED`.

### Scheduling a stage text

When Section 6 rule 6 says a stage text should be created, at time T:

1. Find every message for this RO with kind `STAGE_UPDATE` and status `SCHEDULED`. Set each to `CANCELLED_SUPERSEDED`.
2. Render the text now, using Section 6 rendering rules 1 to 3.
3. Insert 1 message: direction `OUTBOUND`, kind `STAGE_UPDATE`, status `SCHEDULED`, `stage` = the new stage, `scheduled_send_at` = T + `cool_off_minutes`.

### The sender job

The scheduler calls `run_sender(now)` every 60 seconds. Tests call it directly with a frozen clock. It takes every message with status `SCHEDULED` and `scheduled_send_at` at or before `now`, oldest first, and applies these checks in this exact order. The first check that fails decides the outcome.

1. **Consent.** Look up the current consent for (`shop_id`, `to_e164`). No row: set `BLOCKED_NO_CONSENT`. Latest row is `OPTED_OUT`: set `BLOCKED_OPTED_OUT`.
2. **Quiet hours.** Convert `now` to the shop's time zone. Sending is allowed only when `quiet_end` is at or before the local time and the local time is before `quiet_start`. If not allowed, keep `SCHEDULED` and move `scheduled_send_at` to the next `quiet_end`: the same day if local time is before `quiet_end`, otherwise the next day.
3. **Daily cap** (kind `STAGE_UPDATE` only). Count this shop's `OUTBOUND` `STAGE_UPDATE` messages to the same `to_e164` that were sent successfully on the same local calendar date as `now`. If the count is at or above `daily_cap`, keep `SCHEDULED` and move `scheduled_send_at` to the next day's `quiet_end`.
4. **Length.** If the text is over 320 characters, set `FAILED` with `error_text` = `BODY_TOO_LONG`.
5. **Send** through the active provider. Success: set `sent_at` = now and store `provider_message_id`. Status becomes `SENT` in LIVE mode and `DELIVERED` in DEMO mode. Provider error: set `FAILED` with the first 200 characters of the error in `error_text`.

There is no automatic retry. A failed message shows a **Retry** button on the RO page. Retry sets the message back to `SCHEDULED` with `scheduled_send_at` = now.

| Stage change (local time) | After cool-off (10 min) | Sender outcome |
| --- | --- | --- |
| Monday 13:00 | Due Monday 13:10 | Sent Monday 13:10 |
| Monday 19:55 | Due Monday 20:05 | Moved to Tuesday 08:00, sent then |
| Monday 07:30 | Due Monday 07:40 | Moved to Monday 08:00, sent then |

### Manual texts

Staff can type a text of 1 to 320 characters on the RO page and press **Send**. The app inserts a `MANUAL` message with `scheduled_send_at` = now and runs the sender on it immediately. Manual texts skip the cool-off window and the daily cap. They still obey consent, quiet hours and Section 6 rendering rules 2 and 3.

### DEMO-only button: "Send scheduled texts now"

In DEMO mode only, the board header shows this button. It runs the sender on every `SCHEDULED` message as if it were due, ignoring the cool-off window and quiet hours. It still enforces consent, the daily cap and length. The button does not exist in LIVE mode.

### Consent at check-in

The new-RO form shows this script, word for word, above a checkbox:

> Can we text you updates about your repair at this number? We'll only send repair updates. Message and data rates may apply. You can reply STOP anytime to opt out.

Staff tick the box only if the customer says yes, then pick the method: `IN_PERSON_VERBAL` or `SIGNED_FORM`. Ticking it inserts 1 `consents` row with status `OPTED_IN`. An unticked box inserts nothing, so the customer has no consent.

### Inbound texts

Inbound texts arrive in two ways. LIVE: Twilio posts to `POST /webhooks/twilio/inbound` with form fields `From`, `To`, `Body` and `MessageSid`. DEMO: staff use the "Simulate customer reply" box on the RO page, which uses the customer's phone as `From`.

In LIVE mode, the shop is the one whose `twilio_from_e164` equals `To`. No match: return 200 and do nothing else. To read a keyword, trim the body, convert it to uppercase and collapse inner spaces to 1 space. Then compare it to this table, exact match only.

| Set | Exact words | Effect | Automatic reply (kind and text) |
| --- | --- | --- | --- |
| Opt-out | `STOP`, `STOPALL`, `UNSUBSCRIBE`, `CANCEL`, `END`, `QUIT`, `REVOKE`, `OPTOUT`, `OPT OUT` | Insert consent `OPTED_OUT`, method `KEYWORD`. Set every `SCHEDULED` message to that phone from this shop to `BLOCKED_OPTED_OUT`. | `OPT_OUT_CONFIRMATION`: `{shop_name}: You're unsubscribed and won't get more texts from us. Reply START to resubscribe.` |
| Opt-in | `START`, `UNSTOP` | Insert consent `OPTED_IN`, method `KEYWORD` | `OPT_IN_CONFIRMATION`: `{shop_name}: You're resubscribed to repair updates. Reply STOP to opt out.` |
| Help | `HELP`, `INFO` | No consent change | `HELP_REPLY`: `{shop_name} repair updates. Questions? Call {shop_phone}. Msg & data rates may apply. Reply STOP to opt out.` |

A keyword text is stored as `INBOUND`, kind `INBOUND_KEYWORD`, status `RECEIVED`. Its automatic reply goes out immediately and skips the consent, quiet-hours, cool-off and cap checks. In LIVE mode, if `twilio_handles_keyword_replies` is true, the app records the keyword and consent change but sends no reply of its own, because Twilio already replies. In DEMO mode the app always sends its own reply.

Any other inbound text is stored as `INBOUND`, kind `INBOUND_REPLY`, status `RECEIVED`. Attach it to the most recently checked-in active RO for that phone in that shop. If none is active, use the most recently checked-in RO for that phone. If there is none, leave `repair_order_id` null. Set that RO's `needs_reply` to true. Never reply automatically and never use AI.

### Providers

| Provider | Used when | Behavior |
| --- | --- | --- |
| `DemoProvider` | Mode is `DEMO` | Returns `demo-` + UUID4. Prints `[DEMO SMS] to=<masked> body=<text>` to the console. The phone is masked as its first 5 and last 4 characters around `***`, for example `+1818***0123`. |
| `TwilioProvider` | Mode is `LIVE` | Calls `client.messages.create` with `to`, `body`, `from_` = `twilio_from_e164` (or `messaging_service_sid` = `twilio_messaging_service_sid` if that setting is filled) and `status_callback` = `PUBLIC_BASE_URL` + `/webhooks/twilio/status`. Raises an error if mode is not `LIVE` or `ALLOW_LIVE_SMS` is not `true`. |

Twilio status callbacks arrive at `POST /webhooks/twilio/status` with `MessageSid` and `MessageStatus`. Map `delivered` to `DELIVERED`. Map `failed` and `undelivered` to `FAILED`, with the `ErrorCode` value in `error_text`. Ignore every other status.

### LIVE mode preconditions

Switching a shop to LIVE requires all 6 conditions below. If any fails, refuse the switch, list every failed condition on screen, and keep the mode `DEMO`.

1. `ALLOW_LIVE_SMS=true` in `.env`.
2. `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` are set in `.env`.
3. `shop_settings.twilio_from_e164` is a valid E.164 number.
4. `PUBLIC_BASE_URL` is set and starts with `https://`.
5. `shop_settings.a2p_10dlc_approved` is true. An admin ticks this after the Twilio console shows the brand and campaign approved; the app cannot check it.
6. `shop_settings.consent_script_confirmed` is true. An admin ticks this to confirm staff read the consent script at check-in.

The Twilio account must belong to the business, not to an individual. That step happens outside this app.

## 8. Supplement tracker rules

A supplement moves from `DRAFT` to `SUBMITTED` to one final decision, and the app counts business days the whole time it waits. Supplements exist only on `INSURANCE` ROs. Creating one on a `CUSTOMER_PAY` RO fails with `Supplements need an insurance RO.`

### Allowed status changes

Every allowed change inserts 1 `supplement_events` row of type `STATUS_CHANGE`. Any change not in this table fails with `Not allowed: <FROM> -> <TO>`.

| From | To | Required input | Side effects |
| --- | --- | --- | --- |
| (new) | `DRAFT` | Description, requested amount | `sequence_number` = highest on this RO + 1, starting at 1. `adjuster_id` defaults to the RO's adjuster. |
| `DRAFT` | `SUBMITTED` | Submitted time: default now; may be backdated, but not before the RO's `checked_in_at` and not in the future | Sets `submitted_at` and `next_follow_up_due_at` |
| `DRAFT` | `WITHDRAWN` | None | None |
| `SUBMITTED` | `APPROVED` | Decided time: default now; not before `submitted_at`, not in the future | `approved_cents` = `requested_cents`; sets `decided_at`; `next_follow_up_due_at` = null |
| `SUBMITTED` | `PARTIALLY_APPROVED` | Approved amount: more than 0 and less than requested; decided time as above | Sets `approved_cents` and `decided_at`; `next_follow_up_due_at` = null |
| `SUBMITTED` | `DENIED` | Decided time as above | `approved_cents` = 0; sets `decided_at`; `next_follow_up_due_at` = null |
| `SUBMITTED` | `WITHDRAWN` | None | `next_follow_up_due_at` = null |

`APPROVED`, `PARTIALLY_APPROVED`, `DENIED` and `WITHDRAWN` are final. To dispute a partial approval or a denial, staff create a new supplement. Description and requested amount can be edited only while the status is `DRAFT`.

### Business-day math (`app/business_days.py`)

`business_days_between(start, end)`: convert both to the shop's local date. Count every date d where start date < d ≤ end date and d is Monday to Friday. If the end date is on or before the start date, return 0.

| Start (local) | End (local) | Result | Why |
| --- | --- | --- | --- |
| Mon 2026-10-05 10:00 | Thu 2026-10-08 09:00 | 3 | Tue, Wed, Thu count |
| Fri 2026-10-09 16:00 | Mon 2026-10-12 08:00 | 1 | Sat and Sun don't count; Mon does |
| Mon 2026-10-05 10:00 | Mon 2026-10-05 17:00 | 0 | Same date |
| Sat 2026-10-10 11:00 | Mon 2026-10-12 09:00 | 1 | Sun doesn't count; Mon does |

`add_business_days(date, n)` for n of 1 or more: step forward 1 calendar day at a time, counting only Monday to Friday, until n days are counted. Return that date. Examples: Mon 2026-10-05 + 2 = Wed 2026-10-07. Fri 2026-10-09 + 2 = Tue 2026-10-13. Thu 2026-10-08 + 1 = Fri 2026-10-09. Sat 2026-10-10 + 1 = Mon 2026-10-12.

`days_open` = `business_days_between(submitted_at, decided_at)` if decided, otherwise `business_days_between(submitted_at, now)`. It is defined only when `submitted_at` is set.

### Aging buckets

Buckets apply only to supplements with status `SUBMITTED`.

| Bucket | `days_open` | Display color |
| --- | --- | --- |
| `FRESH` | 0 to 1 | Green `#2e7d32` |
| `WATCH` | 2 to 3 | Amber `#f9a825` |
| `LATE` | 4 to 6 | Orange `#ef6c00` |
| `CRITICAL` | 7 or more | Red `#c62828` |

### Follow-up reminders

1. **Interval** = the insurer's `follow_up_interval_business_days` if set, otherwise the shop's `default_follow_up_interval_business_days` (default 2).
2. **On submit:** `next_follow_up_due_at` = `add_business_days(local date of submitted_at, interval)` at `follow_up_due_time` (default 09:00) local, stored in UTC. Example: submitted Mon 2026-10-05 10:00, interval 2: due Wed 2026-10-07 09:00 local, which is 16:00 UTC.
3. **Due:** a supplement is follow-up due when its status is `SUBMITTED` and now is at or after `next_follow_up_due_at`.
4. **Log follow-up** (allowed only while `SUBMITTED`; method required, note optional): `follow_up_count` + 1, `last_follow_up_at` = now, `next_follow_up_due_at` = `add_business_days(local date of now, interval)` at `follow_up_due_time`. Insert 1 `supplement_events` row of type `FOLLOW_UP`. Example: logged Wed 2026-10-07 11:00 local: next due Fri 2026-10-09 09:00 local.
5. Reminders appear on screen only in v1: a count in the top bar, and highlighted rows on the Supplements page (Section 9). No emails or texts go to adjusters.

**Dollars waiting** = the sum of `requested_cents` over all `SUBMITTED` supplements. **Oldest open** = the largest `days_open` among `SUBMITTED` supplements.

### Follow-up email (copied, never sent)

The **Copy follow-up email** button puts this text on the clipboard. The app never sends email.

```
Subject: Supplement S{sequence_number} for claim {claim_number} (RO {ro_number}) - {days_open_phrase}

Hi {adjuster_first_name},

Following up on supplement S{sequence_number} for claim {claim_number}: the {vehicle}, RO {ro_number}. We submitted it on {submitted_date} for {requested_amount}, and it has been waiting {days_open_phrase}.

Could you let us know its status, or anything else you need from us?

Thank you,
{user_full_name}
{shop_name}
{shop_phone}
```

| Placeholder | Rule | Example |
| --- | --- | --- |
| `{days_open_phrase}` | `1 business day` when days open is 1; otherwise the number + `business days` | `8 business days` |
| `{adjuster_first_name}` | First word of the adjuster's `full_name`; `there` if no adjuster | `Dana` |
| `{claim_number}` | The RO's claim number; `(no claim number)` if empty | `CLM-55102` |
| `{submitted_date}` | Local date as weekday abbreviation, month abbreviation, day without a leading zero. Build it by hand, because `%-d` fails on Windows. | `Mon, Oct 5` |
| `{requested_amount}` | Dollars with commas and 2 decimals | `$1,800.00` |
| `{user_full_name}` | The logged-in user's `full_name` | `Leon` |

## 9. Screens and user flows

The app has 9 pages plus 2 webhooks, and every page except Login shows the same top bar. Pages are server-rendered with Jinja2. HTMX swaps small parts of a page (a moved card, a new message) without a full reload.

### Top bar (every page after login)

Left to right: shop name; links to Board, Supplements, Reports, Messages, then Settings and Import for `ADMIN` only; three counters; a mode badge; Log out.

| Counter | Formula |
| --- | --- |
| `Needs reply: N` | Active ROs with `needs_reply` = true |
| `Follow-ups due: N` | Supplements that are follow-up due (Section 8) |
| `Waiting: $X` | Dollars waiting (Section 8), shown with commas and 2 decimals |

The mode badge reads `DEMO` on a gray background or `LIVE` on a red background.

### Pages

| Route | Page | Roles | What it shows and does |
| --- | --- | --- | --- |
| `GET/POST /login` | Login | Public | Email and password. Wrong details show `Email or password is incorrect.` After 10 failed attempts for one email within 15 minutes, every login for that email is refused for 15 minutes after the 10th failure, with `Too many attempts. Try again in 15 minutes.` |
| `GET /` | Job Board | Both | 1 column per active stage, in Section 6 order (12 columns). Details below. |
| `GET/POST /ro/new` | New RO | Both | The new-RO form. Details below. |
| `GET /ro/{id}` | RO detail | Both | Everything about one RO. Details below. |
| `GET /supplements` | Supplements | Both | All supplements with filters. Details below. |
| `GET /reports` | Reports | Both | Date range (from and to local dates; default the 90 days ending today) and the 6 reports in Section 10. |
| `GET /messages` | Message log | Both | All messages, newest first, 50 per page. Filters: direction, status, date range. |
| `GET/POST /settings` | Settings | `ADMIN` | Tabs: Shop, Texting, Supplements, Insurers and adjusters, Users, Mode. Details below. |
| `GET/POST /import` | CSV import | `ADMIN` | Section 11. |
| `POST /webhooks/twilio/inbound` | Webhook | Twilio | Section 7. Returns 404 in DEMO mode. |
| `POST /webhooks/twilio/status` | Webhook | Twilio | Section 7. Returns 404 in DEMO mode. |

### Job Board (`GET /`)

1. Each column lists its ROs oldest check-in first. Columns scroll sideways on small screens.
2. Each card shows: RO number; customer first name and last initial; vehicle; days in shop; payer (insurer name or `Customer pay`).
3. **Days in shop** = whole days from `checked_in_at` to now, rounded down. Example: checked in Thu 2026-10-01 10:00, now Mon 2026-10-05 09:00 = 3.958 days, shown as `3d`.
4. Tags on a card: `Needs reply` if `needs_reply` is true; `No texts` if the customer has no consent or opted out; `Supplement: Nd` showing the largest `days_open` among its `SUBMITTED` supplements, colored by bucket.
5. Each card has a stage dropdown and a **Move** button. HTMX moves the card to its new column. Clicking anywhere else on the card opens the RO page.
6. The header has a **New RO** button. In DEMO mode only, it also has **Send scheduled texts now** (Section 7).

### New RO (`GET/POST /ro/new`)

1. Phone comes first. When it loses focus, HTMX calls `GET /customers/lookup?phone=...`. If that customer exists in this shop, fill in their names and email.
2. Fields: customer first name, last name, email; vehicle year, make, model, color, VIN; payer type; insurer (dropdown); adjuster (dropdown filtered by the chosen insurer, plus **Add adjuster** inline with name, email and phone); claim number; original estimate in dollars; checked-in date and time (default now).
3. Insurer and adjuster fields hide when payer type is `CUSTOMER_PAY`. Only `ADMIN` users can add insurers, in Settings.
4. Consent block: the script from Section 7, the checkbox, and the method choice.
5. Saving creates the customer if new, the RO in stage `CHECKED_IN`, its first `stage_events` row, and the consent row if ticked. Section 6 rule 6 then schedules the check-in text. Redirect to the RO page.

### RO detail (`GET /ro/{id}`)

1. **Header:** RO number, vehicle, customer name and full phone, payer, insurer, adjuster, claim number, original estimate, days in shop, current stage, and an **Edit RO** button. Edit uses the New RO form without the consent block.
2. **Stage control:** dropdown and **Move** button.
3. **Consent panel:** current status (`Opted in`, `Opted out` or `No consent`) with method and date. **Record consent** opens the script and method choice. If the customer opted out by text, the button is disabled with the note `Customer opted out by text. They must text START to resubscribe.`
4. **Messages:** oldest first. Outbound on the right, inbound on the left, each with status and local time. A `SCHEDULED` message shows `Scheduled for HH:MM` and a **Cancel** button, which sets it to `CANCELLED_SUPERSEDED` with `error_text` = `CANCELLED_BY_STAFF`. A `FAILED` message shows **Retry**.
5. **Send a text** box with a live character counter (maximum 320).
6. In DEMO mode only: a **Simulate customer reply** box.
7. **Mark reply handled** button, shown only when `needs_reply` is true. It sets `needs_reply` to false.
8. **Supplements panel** (`INSURANCE` ROs only): columns S#, status, requested, approved, submitted date, days open (bucket color), follow-ups, next due. An **Add supplement** button. Buttons per status: `DRAFT` shows Submit, Withdraw and Edit. `SUBMITTED` shows Approve, Partially approve, Deny, Withdraw, Log follow-up and Copy follow-up email.
9. **Timeline:** stage changes, supplement events and messages merged, newest first.

### Supplements (`GET /supplements`)

1. Filter tabs: **Open** (status `SUBMITTED`, the default), **Draft**, **Decided**, **All**.
2. Summary bar: open count, dollars waiting, count per bucket, follow-ups due.
3. Columns: RO (link), customer, vehicle, insurer, adjuster, S#, requested, days open (bucket color), follow-ups, next follow-up due, actions (Log follow-up, Copy follow-up email).
4. Default sort: days open, highest first; ties by requested amount, highest first.
5. Rows that are follow-up due are bold with a `Due` tag.

### Settings (`ADMIN` only)

| Tab | Contents |
| --- | --- |
| Shop | Name, phone, time zone, address |
| Texting | Quiet hours, cool-off minutes, daily cap, review URL, and per-stage on/off switches and templates. Each template shows a live preview rendered with first name `Maria`, vehicle `2021 Honda Accord`, RO `24-1187`. |
| Supplements | Default follow-up interval and follow-up due time |
| Insurers and adjusters | Add or edit insurers (name, DRP flag, interval override, claims email and phone) and their adjusters |
| Users | Add a user, deactivate a user, reset a password. An admin cannot deactivate themselves. |
| Mode | DEMO or LIVE switch, the 6 preconditions from Section 7 each marked pass or fail, Twilio number, the A2P 10DLC approved checkbox, the consent script confirmed checkbox, and the `twilio_handles_keyword_replies` switch |

### Action routes (all `POST`, all CSRF-protected)

| Route | Does |
| --- | --- |
| `/ro/{id}/stage` | Change stage (Section 6) |
| `/ro/{id}/consent` | Record consent |
| `/ro/{id}/messages` | Send a manual text |
| `/ro/{id}/simulate-reply` | DEMO only: inbound text from the customer's phone. 404 in LIVE mode. |
| `/ro/{id}/mark-handled` | Set `needs_reply` to false |
| `/messages/{id}/retry` | Retry a failed message |
| `/messages/{id}/cancel` | Cancel a scheduled message |
| `/ro/{id}/supplements` | Create a supplement |
| `/supplements/{id}/transition` | Change supplement status (Section 8) |
| `/supplements/{id}/follow-up` | Log a follow-up |
| `/demo/send-now` | DEMO only: send scheduled texts now. 404 in LIVE mode. |

`GET /supplements/{id}/email` returns the follow-up email text for the copy button.

### Roles

| Action | `STAFF` | `ADMIN` |
| --- | --- | --- |
| View board, ROs, supplements, reports, messages | Yes | Yes |
| Create or edit ROs, change stage, record consent, send texts | Yes | Yes |
| Create and change supplements, log follow-ups, add adjusters | Yes | Yes |
| Move an RO out of `DELIVERED` or `CANCELLED` | No | Yes |
| Settings, users, insurers, templates, mode | No | Yes |
| CSV import | No | Yes |

A `STAFF` user who requests an `ADMIN` route gets 403. A record from another shop always returns 404.

## 10. Reports and formulas

The Reports page shows 6 reports, all computed in `app/reports.py` from the formulas below. Report 4 is the one a buyer of the shop would care about most: how much revenue depends on each insurer.

### Shared rules

1. **Date range:** from 00:00:00 on the "from" date to 23:59:59.999 on the "to" date, in the shop's time zone. Each report says which timestamp must fall in the range.
2. **Rounding:** every decimal and percentage shows 1 decimal place, rounded with `ROUND_HALF_UP`.
3. **Median:** sort the values. With an odd count, take the middle one. With an even count, average the 2 middle ones.
4. **Empty groups:** if a figure has no data, show `No data`. Never divide by zero.

### The 6 reports

| # | Report | Included rows | Formula | Shown as |
| --- | --- | --- | --- | --- |
| 1 | Cycle time | ROs with `delivered_at` in range | cycle_days = (`delivered_at` - `checked_in_at`) in seconds / 86,400 | Count, average, median and max, overall and per payer group (each insurer, plus `Customer pay`) |
| 2 | Supplement decision speed | Supplements with `decided_at` in range and status `APPROVED`, `PARTIALLY_APPROVED` or `DENIED` | decision_days = `business_days_between(submitted_at, decided_at)`. Full approval rate = APPROVED count / decided count × 100. Dollar approval rate = sum of `approved_cents` / sum of `requested_cents` × 100. | 2 tables, by insurer and by adjuster (`No adjuster` is its own group). Columns: decided count, average days, median days, full approval rate, dollar approval rate. Slowest average first. |
| 3 | Open supplement aging | All `SUBMITTED` supplements now; ignores the date range | Bucket rules from Section 8 | Count and dollars per bucket, plus a total row |
| 4 | Revenue by payer | ROs with `delivered_at` in range | share = group's sum of `final_invoice_cents` / total × 100 | Revenue and share per payer group, highest first. If any insurer's share is above `concentration_warning_pct` (default 40), show `Concentration warning: <insurer> is <share>% of delivered revenue.` |
| 5 | Days waiting on supplements | ROs with `delivered_at` in range | Per RO: take [`submitted_at`, `decided_at`] for each decided supplement. Merge overlapping intervals, then total merged seconds / 86,400. These are calendar days, because a car sits in a bay on weekends too. | Total waiting days; average per delivered `INSURANCE` RO (total / count of those ROs, including ones with 0 supplements); the 5 ROs with the most waiting days |
| 6 | Texting | Messages with `created_at` in range | Attempted = `OUTBOUND` messages with status `SENT`, `DELIVERED` or `FAILED`. Delivery rate = `DELIVERED` / attempted × 100. | Attempted, delivered, delivery rate, failed, blocked for no consent, blocked for opt-out, opt-outs by keyword (`consents` rows `OPTED_OUT` + `KEYWORD` with `recorded_at` in range), and inbound replies (`INBOUND_REPLY`) |

### How to merge intervals (Report 5)

1. Sort the RO's intervals by start time.
2. Start a merged list with the first interval.
3. For each next interval: if its start is at or before the last merged interval's end, extend that end to the later of the two ends. Otherwise, add it as a new merged interval.
4. Waiting seconds = the sum of (end - start) over the merged list.

Example: [Mon 10-05 10:00, Wed 10-07 10:00] and [Tue 10-06 10:00, Fri 10-09 10:00] merge into [Mon 10-05 10:00, Fri 10-09 10:00], which is 4.0 days.

## 11. CSV import

Admins can load existing repair orders from a CSV file. The import never sends a text, and it saves either every row or none. The flow is always upload, then dry run, then commit.

### Columns

The header row must use these exact names, in any order. An unknown column name rejects the whole file.

| Column | Required | Format | Example | Rule |
| --- | --- | --- | --- | --- |
| `ro_number` | Yes | Text, up to 20 characters | `24-1187` | Must not already exist in the shop or appear twice in the file |
| `checked_in_date` | Yes | `YYYY-MM-DD` | `2026-09-28` | Not in the future |
| `checked_in_time` | No | `HH:MM`, 24-hour | `14:30` | Default `08:00`; date and time are local, converted to UTC |
| `customer_first_name` | Yes | Text, up to 60 characters | `Maria` | |
| `customer_last_name` | No | Text | `Lopez` | |
| `customer_phone` | Yes | Any US format | `(818) 555-0142` | Parsed with `phonenumbers`, region `US`, to E.164. Invalid number: row error. |
| `customer_email` | No | Email | `maria@example.com` | |
| `vehicle_year` | Yes | Integer | `2021` | Same range as Section 5 |
| `vehicle_make` | Yes | Text | `Honda` | |
| `vehicle_model` | Yes | Text | `Accord` | |
| `vehicle_color` | No | Text | `Silver` | |
| `vin` | No | 17 characters | | Same rule as Section 5 |
| `payer_type` | Yes | `INSURANCE` or `CUSTOMER_PAY` | `INSURANCE` | |
| `insurer_name` | If `INSURANCE` | Text | `Northline Insurance (Demo)` | Must match an existing insurer, ignoring case, unless the admin ticks **Create missing insurers** |
| `adjuster_name` | No | Text | `Dana Reyes` | Matched within the insurer, ignoring case; created if missing |
| `claim_number` | No | Text | `CLM-55102` | |
| `original_estimate` | Yes | Dollars | `4250.50` | `$` and `,` are stripped. At most 2 decimals. 0 or more. |
| `stage` | No | A Stage code | `PAINT` | Default `CHECKED_IN`. `DELIVERED` and `CANCELLED` are not allowed. |
| `consent` | No | `YES` or `NO` | `YES` | Default `NO`. `YES` inserts a consent row `OPTED_IN` with method `IMPORTED`. |

### Rules

1. **File limits:** UTF-8 (a byte-order mark is allowed), comma-separated, at most 2 MB and 1,000 data rows. Check size and row count before reading any row.
2. **Existing customers:** a row whose phone matches an existing customer reuses that customer. Their saved names are not overwritten.
3. **Dry run:** uploading shows every row as `OK` or with its list of errors. Row numbers count the header as row 1, so the first data row is row 2. Nothing is saved.
4. **Holding the file:** save the uploaded file as `./import_tmp/<uuid4>.csv` and put the UUID in the commit form. Delete the file after commit, or after 1 hour.
5. **Commit:** the button is enabled only when the dry run shows 0 errors. All rows save in 1 database transaction, so any failure saves nothing. A commit request for a file with errors returns 400.
6. **No texts:** each imported RO gets 1 `stage_events` row from null to its stage, with `note` = `imported` and `changed_by_user_id` = the importing admin. No message rows are created. Because the imported stage counts as already reached (Section 6 rule 6b), the next texting stage after import texts normally.

### Sample file

```
ro_number,checked_in_date,checked_in_time,customer_first_name,customer_last_name,customer_phone,customer_email,vehicle_year,vehicle_make,vehicle_model,vehicle_color,vin,payer_type,insurer_name,adjuster_name,claim_number,original_estimate,stage,consent
24-1187,2026-09-28,09:15,Maria,Lopez,(818) 555-0142,,2021,Honda,Accord,Silver,,INSURANCE,Northline Insurance (Demo),Dana Reyes,CLM-55102,"$4,250.50",PAINT,YES
24-1188,2026-09-29,,James,Carter,818-555-0143,james@example.com,2019,Toyota,Camry,White,,CUSTOMER_PAY,,,,1875.00,PARTS_ORDERED,NO
24-1189,2026-09-30,13:40,Ani,Petrosyan,+1 818 555 0144,,2023,BMW,M3,Blue,,INSURANCE,Harbor Mutual (Demo),,HM-77310,9120,TEARDOWN,YES
```

## 12. Security, privacy and texting compliance

The app keeps each shop's data separate, stores as little personal data as possible, and enforces the texting rules in code rather than relying on staff. This section is a build checklist, not legal advice. Before the first real shop goes LIVE, have a lawyer review the consent script and the templates.

### Security

1. **Passwords:** argon2id hashes, minimum 12 characters.
2. **Sessions:** signed cookie, `HttpOnly`, `SameSite=Lax`, and `Secure` when `PUBLIC_BASE_URL` starts with `https://`. Log the user out after 12 hours with no requests.
3. **CSRF:** every `POST` form carries a per-session token. HTMX sends it in the `X-CSRF-Token` header. A missing or wrong token returns 403. The 2 Twilio webhooks are exempt because they check Twilio's signature instead.
4. **Tenant isolation:** every query filters by the logged-in user's `shop_id`. A record from another shop returns 404, never 403, so its existence is not revealed.
5. **Twilio webhooks:** validate the `X-Twilio-Signature` header with `twilio.request_validator.RequestValidator`, using `TWILIO_AUTH_TOKEN` and the full URL (`PUBLIC_BASE_URL` + path). An invalid signature returns 403 and stores nothing.
6. **Secrets:** only in `.env`. `.env.example` lists every variable name with a placeholder value.
7. **Logs:** never log full phone numbers or message text at `INFO` level. Mask phones as in Section 7.

### Privacy

1. Store only the fields in Section 5. Never store dates of birth, driver's license numbers, insurance policy numbers, payment card data or photos.
2. In v1 the data lives in 1 local SQLite file. Before a real shop uses LIVE mode, the app must be hosted with daily backups (Section 16).
3. Deleting or anonymizing a customer on request is not in v1. It is first on the later roadmap.

### Texting compliance, and where the code enforces it

| Requirement | Enforced by |
| --- | --- |
| Consent before any stage or manual text | Section 7, sender check 1 |
| Opt-out words honored immediately, with at most 1 confirmation | Section 7, inbound keywords |
| The shop is named in every text | Section 6, rendering rule 2 |
| Opt-out instructions in the first text to a number | Section 6, rendering rule 3 |
| No texts from 20:00 to 08:00 by default; settings cannot allow texting before 08:00 or after 21:00 | Section 7, sender check 2, and Section 13 |
| Informational content only, no marketing | Section 6, rendering rule 6 |
| No AI replies, no chatbot, no phone calls | Sections 2 and 7 |
| Carrier registration (A2P 10DLC) before any real text | Section 7, LIVE precondition 5 |

v1 has no automated conversations, so California's bot disclosure law (SB 1001) does not come into play. If an AI reply feature is ever added, every AI-written text must say it is automated.

## 13. Configuration

Shop-level settings live in the `shop_settings` table and are edited on the Settings page. Server-level settings live in `.env`. A value outside its allowed range is rejected on save with an error naming the field and its range.

### `shop_settings` fields

| Field | Type | Default | Allowed values | Used in |
| --- | --- | --- | --- | --- |
| `messaging_mode` | `MessagingMode` | `DEMO` | `DEMO`, `LIVE` | Section 7 |
| `quiet_start` | time `HH:MM` | `20:00` | `12:00` to `21:00` | Section 7 |
| `quiet_end` | time `HH:MM` | `08:00` | `08:00` to `11:00` | Section 7 |
| `cool_off_minutes` | integer | 10 | 0 to 60 | Section 7 |
| `daily_cap` | integer | 3 | 1 to 10 | Section 7 |
| `review_url` | string | empty | Empty, or starts with `https://` | Section 6 |
| `stage_text_enabled` | JSON object, stage code to true/false | The "Texts by default" column in Section 6 | `CANCELLED` is always false | Section 6 |
| `stage_templates` | JSON object, stage code to text | The default templates in Section 6 | Up to 250 characters; known variables only | Section 6 |
| `default_follow_up_interval_business_days` | integer | 2 | 1 to 10 | Section 8 |
| `follow_up_due_time` | time `HH:MM` | `09:00` | `06:00` to `17:00` | Section 8 |
| `concentration_warning_pct` | integer | 40 | 10 to 90 | Section 10 |
| `twilio_from_e164` | string | empty | Empty, or a valid E.164 number | Section 7 |
| `twilio_messaging_service_sid` | string | empty | Empty, or starts with `MG` | Section 7 |
| `a2p_10dlc_approved` | boolean | false | | Section 7 |
| `consent_script_confirmed` | boolean | false | | Section 7 |
| `twilio_handles_keyword_replies` | boolean | true | | Section 7 |

### Environment variables (`.env`)

| Name | Required | Example | Purpose |
| --- | --- | --- | --- |
| `APP_SECRET_KEY` | Always | 64 random hex characters | Signs session cookies and CSRF tokens. If missing or shorter than 32 characters, the app prints an error and exits with code 1. |
| `DATABASE_URL` | Always | `sqlite:///./bodyshop.db` | |
| `ALLOW_LIVE_SMS` | Always | `false` | Must be `true` for LIVE mode (Section 7) |
| `SCHEDULER_ENABLED` | Always | `true` | Tests set `false` and call `run_sender(now)` directly |
| `LOG_LEVEL` | No | `INFO` | |
| `TWILIO_ACCOUNT_SID` | LIVE only | `ACxxxxxxxx` | |
| `TWILIO_AUTH_TOKEN` | LIVE only | (secret) | Also used to check webhook signatures |
| `PUBLIC_BASE_URL` | LIVE only | `https://shop.example.com` | Builds webhook URLs and checks signatures |

## 14. Demo data and the pitch demo

`python -m app.seed --demo` builds a fake shop that makes the pitch demo work every time, fully offline, in DEMO mode. Everything random uses `random.Random(42)`, and every date is placed relative to the moment the seed runs. If a shop named `Brand Blvd Collision (Demo)` already exists, the command prints `Demo shop already exists.` and exits with code 1 without changing anything.

### Demo data contents

| Item | Exact content |
| --- | --- |
| Shop | `Brand Blvd Collision (Demo)`, phone `+18185550100`, time zone `America/Los_Angeles`, mode `DEMO`, review URL `https://example.com/review` |
| Users | `admin@demo.local` (`ADMIN`, "Demo Admin"), `staff1@demo.local` (`STAFF`, "Sam Rivera"), `staff2@demo.local` (`STAFF`, "Lena Park"). All use password `demo-password-123`, printed to the console at the end. |
| Insurers (all fictional) | `Northline Insurance (Demo)` DRP yes; `Harbor Mutual (Demo)` DRP yes; `Summit Auto Insurance (Demo)` DRP no, interval override 3; `Coastal General (Demo)` DRP no; `Valley Shield (Demo)` DRP no |
| Adjusters | 2 per insurer, from this list in order: Dana Reyes, Chris Patel, Morgan Lee, Jordan Brooks, Taylor Nguyen, Casey Morales, Riley Adams, Jamie Chen, Avery Scott, Quinn Harper |
| Phone numbers | Customers use `+18185550101` to `+18185550199`. The 555-0100 to 555-0199 range is reserved for fictional use. |
| Repair orders | 45 total. 30 active, spread over the 12 active stages with at least 1 per stage and at least 4 in `PAINT`. 15 delivered, with `delivered_at` in the last 60 days. 11 are `CUSTOMER_PAY`. Northline gets over 40% of delivered revenue, so the concentration warning appears. |
| Supplements | 18 total. 8 `SUBMITTED`, with `days_open` of exactly 0, 1, 2, 3, 4, 6, 8 and 11 (2 per bucket), built by counting business days backward from today at 10:00 local. Then 6 `APPROVED`, 2 `PARTIALLY_APPROVED`, 1 `DENIED`, 1 `DRAFT`. Harbor Mutual's decided supplements average 4 to 5 business days and Northline's 1 to 2. Exactly 3 `SUBMITTED` supplements are follow-up due. |
| Consent | 40 customers `OPTED_IN` (`IN_PERSON_VERBAL`), 1 `OPTED_OUT` by `KEYWORD`, the rest with no consent |
| Messages | Every stage text each RO would have produced, with status `DELIVERED` for consenting customers and the matching `BLOCKED_` status for the others |
| Inbound replies | 2 ROs with `needs_reply` = true and inbound texts `When will it be ready?` and `Can I pick it up Saturday?` |
| Scheduled | 1 `SCHEDULED` stage text due 5 minutes after the seed runs, so the demo button has something to send |

### The 5-minute pitch demo

The app must support this exact walkthrough, start to finish, with no errors and no internet connection. Log in as `admin@demo.local`.

1. **Board.** Point at `Follow-ups due: 3` and the `Waiting: $X` counter. Say: "Every car in your shop and every dollar stuck with insurers, on one screen."
2. **Supplements page.** The top row is `CRITICAL` at 11 business days. Say: "This car has sat in a bay for 11 business days waiting on one adjuster."
3. Click **Copy follow-up email**, then **Log follow-up** with method Phone. The row loses its `Due` tag and shows a new next-due date.
4. Open an RO in `PARTS_RECEIVED` and move it to `BODY_REPAIR`. The thread shows `Scheduled for HH:MM`. Click **Send scheduled texts now** on the board. The text appears as delivered.
5. In **Simulate customer reply**, type `Thanks! What time can I pick it up?`. The RO gets the `Needs reply` tag, and the top bar count goes up by 1.
6. **Reports.** Report 2 shows Harbor Mutual is slower than Northline. Report 4 shows the concentration warning. Report 5 shows the total days cars sat waiting.
7. Close: "This runs every day for $300 a month. Try it free for 30 days."

## 15. Build phases and acceptance tests

The build has 8 phases, 0 to 7, with 78 automated tests and 1 manual check. Each test ID below becomes at least 1 pytest test. A phase passes only when all of its tests pass and all earlier phases still pass.

### Test conventions

1. Freeze the clock with `freezegun` in every time-based test.
2. "Local" means `America/Los_Angeles`. Every October 2026 date before November 1 is PDT, which is UTC-7, so 08:00 local = 15:00 UTC. Assert UTC values exactly as written here.
3. Set `SCHEDULER_ENABLED=false` and call `run_sender(now)` directly.
4. Give every test a fresh SQLite database in pytest's `tmp_path`, built with `build_base_fixture()` from `tests/fixtures.py`.
5. "Delivered" in a DEMO-mode test means status `DELIVERED`.

### Base fixture (`build_base_fixture()`)

| Record | Exact values |
| --- | --- |
| Shop | `Test Collision`, phone `+18185550100`, time zone `America/Los_Angeles`, all Section 13 defaults |
| Users | `admin@test.local` (`ADMIN`, "Test Admin"), `staff@test.local` (`STAFF`, "Test Staff"), both with password `test-password-123` |
| Second shop | `Other Shop` with user `other@test.local` (`ADMIN`), for isolation tests |
| Insurers | `Alpha Insurance` (DRP yes), `Beta Insurance` (DRP no) |
| Adjusters | `Dana Reyes` (Alpha), `Sam Ortiz` (Beta) |
| Customer 1 | Maria Lopez, `+18185550142`, consent `OPTED_IN` (`IN_PERSON_VERBAL`) recorded 2026-10-01 09:00 local |
| Customer 2 | James Carter, `+18185550143`, no consent |
| RO 24-1187 | Maria, 2021 Honda Accord, `INSURANCE`, Alpha, Dana Reyes, claim `CLM-55102`, estimate 425050, checked in 2026-10-01 09:00 local, stage `CHECKED_IN`. Its check-in text already exists with status `DELIVERED`, sent 2026-10-01 09:10 local. |
| RO 24-1188 | James, 2019 Toyota Camry, `CUSTOMER_PAY`, estimate 187500, checked in 2026-10-02 09:00 local, stage `CHECKED_IN` |

### Phase 0: scaffold, database, login

| ID | Setup and action | Pass when |
| --- | --- | --- |
| T0.1 | Run migrations on an empty database | Table names, excluding `alembic_version`, equal exactly the 13 in Section 5 |
| T0.2 | Log in with right and wrong passwords | Right: 302 to `/`. Wrong: 200 and the page contains `Email or password is incorrect.` |
| T0.3 | 10 wrong logins for `staff@test.local`, then the right password; then freeze 15 min 1 s after the 10th failure and try again | Attempt 11 shows `Too many attempts. Try again in 15 minutes.` The later attempt returns 302. |
| T0.4 | `GET /settings` as staff, then as admin | Staff: 403. Admin: 200. |
| T0.5 | `other@test.local` requests `GET /ro/<id of 24-1187>` | 404 |
| T0.6 | Money helpers | `"4,250.50"` gives 425050. `"$1,800"` gives 180000. `"12.345"` raises an error. 317050 formats as `$3,170.50`. |
| T0.7 | Start the app with no `APP_SECRET_KEY` | Exit code 1 |
| T0.8 | Any `POST` without a CSRF token | 403 |

### Phase 1: repair orders and board

| ID | Setup and action | Pass when |
| --- | --- | --- |
| T1.1 | Create RO 24-0001 for Maria at 2026-10-05 13:00 local | Stage `CHECKED_IN`; exactly 1 `stage_events` row, `from_stage` null |
| T1.2 | Move 24-1187: `CHECKED_IN` to `TEARDOWN` to `PAINT` | `stage_events` for the RO = 1 + 2 = 3; stage `PAINT` |
| T1.3 | Move it to `PAINT` again | Still 3 events |
| T1.4 | Move to `DELIVERED` with no final invoice; then with `final_invoice_cents` 512300 at 2026-10-09 16:00 local | First: error, stage stays `PAINT`. Second: `delivered_at` = 2026-10-09T23:00:00Z |
| T1.5 | Move the delivered RO to `PAINT` as staff, then as admin | Staff: 403. Admin: allowed and `delivered_at` = null |
| T1.6 | Create a `CUSTOMER_PAY` RO with an insurer set | Validation error; RO count unchanged |
| T1.7 | Board with 5 active, 2 delivered, 1 cancelled ROs | 5 cards |
| T1.8 | RO checked in 2026-10-01 10:00 local; now 2026-10-05 09:00 local | Card shows `3d` (3.958 rounded down) |
| T1.9 | VIN `1hgcv1f3xla000001`; then VIN `1HGCV1F3OLA000001` | First saved as `1HGCV1F3XLA000001`. Second rejected (contains O). |

### Phase 2: messaging engine, DEMO provider

All times are local on Monday 2026-10-05 unless stated, using RO 24-1187.

| ID | Setup and action | Pass when |
| --- | --- | --- |
| T2.1 | Move to `PARTS_ORDERED` at 13:00. Run the sender at 13:09, then at 13:10. | 1 message, `scheduled_send_at` = 2026-10-05T20:10:00Z. Still `SCHEDULED` after 13:09. `DELIVERED` after 13:10, with `sent_at` = 2026-10-05T20:10:00Z. |
| T2.2 | `PARTS_ORDERED` at 13:00, `BODY_REPAIR` at 13:04, run the sender at 13:14 | The `PARTS_ORDERED` message is `CANCELLED_SUPERSEDED`. Exactly 1 `BODY_REPAIR` message, `DELIVERED`. |
| T2.3 | `PARTS_ORDERED` at 19:55, run the sender at 20:05, then at 08:00 on 2026-10-06 | After 20:05: still `SCHEDULED`, `scheduled_send_at` = 2026-10-06T15:00:00Z. After 08:00: `DELIVERED`. |
| T2.4 | `PARTS_ORDERED` at 07:30, run the sender at 07:40, then at 08:00 | After 07:40: `scheduled_send_at` = 2026-10-05T15:00:00Z. After 08:00: `DELIVERED`. |
| T2.5 | Move RO 24-1188 (James, no consent) to `PAINT` at 13:00; run the sender at 13:10; spy on `DemoProvider.send` | The `PAINT` message is `BLOCKED_NO_CONSENT`; `send` called 0 times |
| T2.6 | Leave 1 message `SCHEDULED`, then simulate inbound `stop` | Latest consent `OPTED_OUT`. The scheduled message is `BLOCKED_OPTED_OUT`. Exactly 1 `OPT_OUT_CONFIRMATION`, `DELIVERED`. |
| T2.7 | After T2.6, inbound `Start`; then move to `PAINT` at 13:00 and run the sender at 13:10 | Consent `OPTED_IN`. Exactly 1 `OPT_IN_CONFIRMATION`. The `PAINT` message is `DELIVERED`. |
| T2.8 | Inbound `help` | Exactly 1 `HELP_REPLY`; `consents` row count unchanged |
| T2.9 | Moves at 09:00 (`PARTS_ORDERED`), 10:00 (`BODY_REPAIR`), 11:00 (`PAINT`), 12:00 (`READY_FOR_PICKUP`); run the sender 10 minutes after each | 3 `STAGE_UPDATE` messages delivered on 2026-10-05 local. The 4th is `SCHEDULED` with `scheduled_send_at` = 2026-10-06T15:00:00Z. |
| T2.10 | `PAINT` at 13:00, `BODY_REPAIR` at 14:00, `PAINT` at 15:00, running the sender after each | Exactly 1 `PAINT` message and 1 `BODY_REPAIR` message on the RO |
| T2.11 | Move to `TEARDOWN` | 0 new messages |
| T2.12 | New opted-in customer `+18185550144` with no prior messages; set the `CHECKED_IN` template to `{shop_name}: Your {vehicle} is checked in.`; create an RO and run the sender; then move to `PARTS_ORDERED` and run it again | First text ends with `Reply STOP to opt out.` Second text does not contain `Reply STOP to opt out.` |
| T2.13 | Manual text of exactly 320 `x` characters at 13:00 | Body = `Test Collision: ` (16 characters) + 320 = 336 characters, so status is `FAILED` with `error_text` = `BODY_TOO_LONG` |
| T2.14 | Inbound `When will it be ready?` from Maria | 1 message `INBOUND_REPLY`, `RECEIVED`, attached to 24-1187; `needs_reply` = true; outbound count unchanged |
| T2.15 | Manual text at 21:00 | `SCHEDULED` with `scheduled_send_at` = 2026-10-06T15:00:00Z |
| T2.16 | Render `{shop_name}: Your {vehicle} is in paint.` for 24-1187 | Exactly `Test Collision: Your 2021 Honda Accord is in paint.` |
| T2.17 | Move to `PARTS_ORDERED` at 2026-10-31 19:55 PDT; run the sender at 20:05 PDT | `scheduled_send_at` = 2026-11-01T16:00:00Z (08:00 PST, after daylight saving ends) |
| T2.18 | Send to `+18185550142` in DEMO mode and capture the console | The line contains `+1818***0142` and does not contain `+18185550142` |

### Phase 3: supplement tracker

All supplements are on RO 24-1187 (Alpha, default interval 2) unless stated.

| ID | Setup and action | Pass when |
| --- | --- | --- |
| T3.1 | Create a supplement on 24-1188 (`CUSTOMER_PAY`) | Error `Supplements need an insurance RO.` |
| T3.2 | Create 3 supplements | `sequence_number` values are 1, 2, 3 |
| T3.3 | Change `DRAFT` directly to `APPROVED` | Error `Not allowed: DRAFT -> APPROVED` |
| T3.4 | `business_days_between` on the 4 rows in Section 8 | 3, 1, 0, 1 |
| T3.5 | `add_business_days` on the 4 examples in Section 8 | Wed 2026-10-07, Tue 2026-10-13, Fri 2026-10-09, Mon 2026-10-12 |
| T3.6 | Submit at Mon 2026-10-05 10:00 local; repeat with Alpha's interval override set to 3 | `next_follow_up_due_at` = 2026-10-07T16:00:00Z; with override, 2026-10-08T16:00:00Z |
| T3.7 | After T3.6 (interval 2), check at 2026-10-07 08:59 local and at 09:00 | 08:59: not due. 09:00: due, and the top bar shows `Follow-ups due: 1`. |
| T3.8 | Log a follow-up at Wed 2026-10-07 11:00 local | `follow_up_count` = 1; `next_follow_up_due_at` = 2026-10-09T16:00:00Z; not due |
| T3.9 | Requested 180000; partially approve 120000, then try 180000, then 0 | 120000 accepted. 180000 rejected. 0 rejected. |
| T3.10 | Submit Mon 2026-10-05 10:00; approve Thu 2026-10-08 14:00 local | `approved_cents` = `requested_cents`; `decided_at` = 2026-10-08T21:00:00Z; `next_follow_up_due_at` = null; `days_open` = 3 |
| T3.11 | Bucket for `days_open` 0, 1, 2, 3, 4, 6, 7, 11 | FRESH, FRESH, WATCH, WATCH, LATE, LATE, CRITICAL, CRITICAL |
| T3.12 | `SUBMITTED` supplements of 180000, 95050 and 42000, plus 1 `APPROVED` of 50000 | Dollars waiting = 180000 + 95050 + 42000 = 317050, shown as `$3,170.50` |
| T3.13 | S1 submitted Mon 2026-10-05 10:00 for 180000; now Fri 2026-10-09 12:00 local; logged in as Test Admin | Subject is exactly `Supplement S1 for claim CLM-55102 (RO 24-1187) - 4 business days`. Body contains `Hi Dana,` and `We submitted it on Mon, Oct 5 for $1,800.00, and it has been waiting 4 business days.` |
| T3.14 | Submit with a time before the RO's check-in; then with a future time | Both rejected |

### Phase 4: reports

Freeze now at 2026-10-20 12:00 local and use the default 90-day range. Build this fixture on top of the base fixture:

| RO | Payer | Checked in (local) | Delivered (local) | Final invoice (cents) |
| --- | --- | --- | --- | --- |
| R-1 | Alpha Insurance | 2026-10-01 08:00 | 2026-10-12 20:00 | 500000 |
| R-2 | Customer pay | 2026-10-02 09:00 | 2026-10-05 09:00 | 200000 |
| R-3 | Beta Insurance | 2026-10-01 12:00 | 2026-10-15 12:00 | 300000 |

| Supplement | RO | Adjuster | Submitted (local) | Decided (local) | Status | Requested | Approved |
| --- | --- | --- | --- | --- | --- | --- | --- |
| S-A1 | R-1 | Dana Reyes | Mon 2026-10-05 10:00 | Wed 2026-10-07 10:00 | `APPROVED` | 100000 | 100000 |
| S-A2 | R-1 | Dana Reyes | Tue 2026-10-06 10:00 | Fri 2026-10-09 10:00 | `PARTIALLY_APPROVED` | 50000 | 30000 |
| S-B1 | R-3 | Sam Ortiz | Tue 2026-10-06 10:00 | Tue 2026-10-13 10:00 | `DENIED` | 80000 | 0 |

| ID | Check | Arithmetic | Pass when |
| --- | --- | --- | --- |
| T4.1 | Cycle time | R-1 = 11.5, R-2 = 3.0, R-3 = 14.0. Average = (11.5 + 3.0 + 14.0) / 3 | Average 9.5, median 11.5, max 14.0 |
| T4.2 | Alpha decision speed | S-A1 = 2 days, S-A2 = 3 days. Dollar rate = (100000 + 30000) / (100000 + 50000) × 100 = 86.67 | Average 2.5, median 2.5, full approval 50.0%, dollar approval 86.7% |
| T4.3 | Beta decision speed | S-B1 = 5 days. Dollar rate = 0 / 80000 × 100 | Average 5.0, full approval 0.0%, dollar approval 0.0%, and Beta is the first row (slowest) |
| T4.4 | By adjuster | Same groups as insurers | Dana Reyes average 2.5; Sam Ortiz 5.0 |
| T4.5 | Revenue by payer | Total = 500000 + 200000 + 300000 = 1000000 | Alpha 50.0%, Beta 30.0%, Customer pay 20.0%. Warning text is exactly `Concentration warning: Alpha Insurance is 50.0% of delivered revenue.` |
| T4.6 | Days waiting | R-1 merged = 4.0 days, R-3 = 7.0 days. Average = 11.0 / 2 delivered insurance ROs | Total 11.0, average 5.5 |
| T4.7 | Open aging | Add a `SUBMITTED` supplement submitted Thu 2026-10-08 10:00. Business days after 10-08 up to 10-20: 8 | 1 supplement in `CRITICAL`; other buckets 0 |
| T4.8 | Texting | Range 2026-10-19 to 2026-10-20, with 10 outbound messages created on 2026-10-19: 8 `DELIVERED`, 1 `SENT`, 1 `FAILED`. Rate = 8 / 10 × 100 | Attempted 10, delivery rate 80.0% |
| T4.9 | Empty range 2026-01-01 to 2026-01-31 | No rows | Every empty figure shows `No data`; no exception |
| T4.10 | Median helper | [2, 3] and [2, 3, 5] | 2.5 and 3 |

### Phase 5: CSV import

Delete the base fixture's 2 ROs (24-1187 and 24-1188) first, because the sample file reuses those RO numbers. Then create insurers `Northline Insurance (Demo)` and `Harbor Mutual (Demo)`, then use the sample file from Section 11.

| ID | Setup and action | Pass when |
| --- | --- | --- |
| T5.1 | Dry run, then commit the sample file | Dry run: 3 rows `OK`. Commit: 3 new ROs, 3 `stage_events` with note `imported`, 0 messages, 2 consents with method `IMPORTED`. Estimates 425050, 187500, 912000. |
| T5.2 | Change the second data row's phone to `123` | Dry run shows an error on row 3. A commit request returns 400; 0 new ROs. |
| T5.3 | Repeat a `ro_number` in 2 rows | Error on the second occurrence |
| T5.4 | File with 1,001 data rows | Rejected before any row is read |
| T5.5 | Add a column named `color2` | Whole file rejected |
| T5.6 | Move imported RO 24-1187 (`PAINT`) to `READY_FOR_PICKUP`, then back to `PAINT` | First move: 1 `STAGE_UPDATE` message. Second move: 0. |
| T5.7 | Insurer `Zeta Insurance` without, then with, **Create missing insurers** | Without: row error. With: insurer created and the row is `OK`. |
| T5.8 | A row with stage `DELIVERED` | Row error |

### Phase 6: settings and LIVE mode (Twilio mocked)

Tests never call the real Twilio API. Mock the Twilio client.

| ID | Setup and action | Pass when |
| --- | --- | --- |
| T6.1 | Switch to LIVE with `ALLOW_LIVE_SMS=false` | Refused; precondition 1 listed as failed; mode stays `DEMO` |
| T6.2 | All 6 preconditions true, `twilio_from_e164` = `+18185550188`; send a stage text to Maria | Mode `LIVE`. The mock `messages.create` is called exactly 1 time with `to` = `+18185550142` and `from_` = `+18185550188`. Status `SENT`. |
| T6.3 | Call `TwilioProvider.send` while mode is `DEMO` | Raises an error |
| T6.4 | Inbound webhook with a bad signature; then a valid signature (computed in the test) with body `STOP` and `twilio_handles_keyword_replies` = true | Bad: 403, 0 messages stored. Valid: 200, consent `OPTED_OUT`, 0 outbound replies. |
| T6.5 | Status callbacks `delivered`, then `undelivered` with `ErrorCode` 30003, for 2 messages | First `DELIVERED`. Second `FAILED` with `error_text` = `30003`. |
| T6.6 | Save `quiet_start` 22:00; `quiet_end` 07:00; `cool_off_minutes` 61; `daily_cap` 0; a 251-character template; a template containing `{nickname}` | All 6 rejected; the last with `Unknown variable {nickname}` |
| T6.7 | Call both webhooks in DEMO mode | Both 404 |
| T6.8 | Live preview of the default `CHECKED_IN` template | Exactly `Hi Maria, this is Test Collision. Your 2021 Honda Accord is checked in (RO 24-1187). We'll text you as the repair moves along. Reply STOP to opt out, HELP for help.` |

### Phase 7: demo seed and pitch walkthrough

| ID | Setup and action | Pass when |
| --- | --- | --- |
| T7.1 | Run `python -m app.seed --demo` on a fresh database | Exit 0. Counts: 1 shop, 3 users, 5 insurers, 10 adjusters, 45 ROs, 18 supplements. The 8 `SUBMITTED` supplements have `days_open` {0, 1, 2, 3, 4, 6, 8, 11}. Follow-ups due = 3. |
| T7.2 | Run it again | Exit 1; every count unchanged |
| T7.3 | Open Reports with the default range | The concentration warning names `Northline Insurance (Demo)` |
| Manual | Turn off Wi-Fi and run the 7-step pitch demo in Section 14 | Every step works, no error page appears, and the run takes under 5 minutes |

**v1 is done** when `pytest -q` reports 78 or more passing tests with 0 failures, and the manual check passes.

## 16. Out of scope for v1, and what comes later

Nothing below gets built until v1 passes every test and a real shop is using it. The list is in build order.

| Order | Feature | Why it waits |
| --- | --- | --- |
| 1 | Hosting with Postgres and daily backups | Required before any real shop goes LIVE |
| 2 | Delete or anonymize a customer on request | Handles privacy requests properly |
| 3 | Daily email digest of follow-ups due | Needs email sending, which v1 does not have |
| 4 | Pull ROs and stages from estimating software (CCC ONE, Mitchell, Audatex) | Each vendor requires partner approval. It also removes manual updates, the main reason staff stop using tools like this. |
| 5 | Photo updates by picture message ("your car in paint") | Higher cost per text |
| 6 | Review request after delivery | Separate consent and content rules |
| 7 | AI-drafted adjuster follow-up emails that staff approve before sending | Needs a careful review step |
| 8 | Customer status page linked from texts | Adds customer-facing security work |
| 9 | Multiple locations per shop | Rare for the target customer |
| 10 | Bundle with missed-call text-back and an AI receptionist | That is a separate product, sold to the same shops later |
