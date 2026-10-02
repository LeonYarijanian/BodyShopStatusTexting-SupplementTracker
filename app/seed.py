"""Demo data (Section 14): `python -m app.seed --demo` builds a fake shop for the offline pitch demo.

Everything random uses random.Random(42), and every date is placed relative to the moment the seed runs.
"""

import argparse
import datetime as dt
import random
import sys
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.business_days import add_business_days, is_business_day, local_datetime_at, local_to_utc, to_local, utcnow
from app.config import Settings
from app.db import make_engine, make_session_factory
from app.enums import (
    ConsentMethod,
    ConsentStatus,
    FollowUpMethod,
    MessageDirection,
    MessageKind,
    MessageStatus,
    MessagingMode,
    PayerType,
    Role,
    Stage,
    SupplementEventType,
    SupplementStatus,
)
from app.messaging.engine import render_stage_text, stage_template
from app.messaging.inbound import OPT_OUT_REPLY
from app.messaging.templates import DEFAULT_TEXT_ENABLED, replace_variables, template_variables
from app.models import (
    Adjuster,
    Consent,
    Customer,
    Insurer,
    Message,
    RepairOrder,
    Shop,
    ShopSettings,
    StageEvent,
    Supplement,
    SupplementEvent,
    User,
)

DEMO_SHOP_NAME = "Brand Blvd Collision (Demo)"
DEMO_PASSWORD = "demo-password-123"
TZ = "America/Los_Angeles"

NORTHLINE = "Northline Insurance (Demo)"
HARBOR = "Harbor Mutual (Demo)"
SUMMIT = "Summit Auto Insurance (Demo)"
COASTAL = "Coastal General (Demo)"
VALLEY = "Valley Shield (Demo)"
INSURERS = [  # name, DRP, follow-up interval override
    (NORTHLINE, True, None),
    (HARBOR, True, None),
    (SUMMIT, False, 3),
    (COASTAL, False, None),
    (VALLEY, False, None),
]
ADJUSTER_NAMES = [
    "Dana Reyes", "Chris Patel", "Morgan Lee", "Jordan Brooks", "Taylor Nguyen",
    "Casey Morales", "Riley Adams", "Jamie Chen", "Avery Scott", "Quinn Harper",
]  # fmt: skip
CLAIM_PREFIX = {NORTHLINE: "NL", HARBOR: "HM", SUMMIT: "SA", COASTAL: "CG", VALLEY: "VS"}

FIRST_NAMES = [
    "Maria", "James", "Ani", "David", "Sofia", "Michael", "Emily", "Daniel", "Olivia", "Jose", "Grace", "Kevin",
    "Hannah", "Luis", "Chloe", "Brian", "Nora", "Arman", "Leah", "Eric", "Isabel", "Tigran", "Megan", "Carlos",
    "Rachel", "Steven", "Lucia", "Andrew", "Vanessa", "Hector", "Natalie", "Gabriel", "Diana", "Samuel", "Karen",
    "Victor", "Alicia", "Marcus", "Elena", "Ryan", "Anahit", "Jason", "Paula", "Omar", "Teresa", "Adam", "Lilit",
]  # fmt: skip
LAST_NAMES = [
    "Lopez", "Carter", "Petrosyan", "Kim", "Garcia", "Nguyen", "Hakobyan", "Martinez", "Johnson", "Sarkisian",
    "Rivera", "Thompson", "Hernandez", "Wong", "Avetisyan", "Brown", "Ramirez", "Davis", "Torres", "Mkrtchyan",
    "Flores", "Wilson", "Gonzalez", "Lee", "Morales", "Clark", "Reyes", "Lewis", "Castillo", "Walker",
]  # fmt: skip
VEHICLES = [
    ("Honda", "Accord"), ("Toyota", "Camry"), ("Toyota", "RAV4"), ("Honda", "Civic"), ("Tesla", "Model 3"),
    ("Ford", "F-150"), ("Nissan", "Altima"), ("BMW", "3 Series"), ("Hyundai", "Elantra"), ("Kia", "Sorento"),
    ("Subaru", "Outback"), ("Mercedes-Benz", "C-Class"), ("Lexus", "RX 350"), ("Chevrolet", "Equinox"),
    ("Mazda", "CX-5"), ("Jeep", "Grand Cherokee"), ("Audi", "Q5"), ("Volkswagen", "Jetta"),
]  # fmt: skip
COLORS = ["Silver", "White", "Black", "Gray", "Blue", "Red", "Pearl White", "Dark Green"]
SUPPLEMENT_DESCRIPTIONS = [
    "Hidden damage: RF apron, headlamp bracket",
    "Bumper reinforcement bent behind cover",
    "Radiator support cracked; A/C condenser",
    "Frame pull and measuring after teardown",
    "ADAS camera recalibration after windshield",
    "Inner quarter panel damage behind liner",
    "Hood hinge and latch replacement",
    "Suspension: LF control arm and tie rod",
    "Wheel alignment plus TPMS sensor",
    "Additional blend panels for color match",
]

INS = PayerType.INSURANCE
CP = PayerType.CUSTOMER_PAY
S = SupplementStatus


@dataclass
class SupplementPlan:
    status: SupplementStatus
    days_open: int | None = None  # SUBMITTED: business days open now
    decision_days: int | None = None  # decided: business days from submit to decision
    due: bool = False  # SUBMITTED: follow-up due now


@dataclass
class ROPlan:
    stage: Stage
    payer: PayerType
    insurer: str | None
    days_ago: int  # active: check-in this many days before today; delivered: delivered this many days ago
    consent: str = "in"  # "in", "none" or "out" (opted out by keyword)
    supplements: list[SupplementPlan] = field(default_factory=list)
    inbound: str | None = None  # an inbound reply that needs a reply
    scheduled: bool = False  # the last stage text is still SCHEDULED, due 5 minutes after the seed runs


ACTIVE = [
    ROPlan(Stage.CHECKED_IN, INS, NORTHLINE, 1),
    ROPlan(Stage.CHECKED_IN, INS, VALLEY, 2),
    ROPlan(Stage.CHECKED_IN, CP, None, 1, consent="none"),
    ROPlan(Stage.WAITING_ON_INSURANCE, INS, HARBOR, 3, inbound="When will it be ready?"),
    ROPlan(Stage.WAITING_ON_INSURANCE, INS, COASTAL, 4),
    ROPlan(Stage.WAITING_ON_INSURANCE, INS, SUMMIT, 5),
    ROPlan(Stage.TEARDOWN, INS, NORTHLINE, 4, supplements=[SupplementPlan(S.DRAFT)]),
    ROPlan(Stage.TEARDOWN, INS, VALLEY, 5),
    ROPlan(Stage.SUPPLEMENT_PENDING, INS, HARBOR, 21, supplements=[SupplementPlan(S.SUBMITTED, days_open=11, due=True)]),
    ROPlan(Stage.SUPPLEMENT_PENDING, INS, NORTHLINE, 16, supplements=[SupplementPlan(S.SUBMITTED, days_open=8, due=True)]),
    ROPlan(Stage.SUPPLEMENT_PENDING, INS, HARBOR, 13, supplements=[SupplementPlan(S.SUBMITTED, days_open=6, due=True)]),
    ROPlan(Stage.SUPPLEMENT_PENDING, INS, COASTAL, 9, supplements=[SupplementPlan(S.SUBMITTED, days_open=4)]),
    ROPlan(Stage.PARTS_ORDERED, INS, SUMMIT, 8, supplements=[SupplementPlan(S.SUBMITTED, days_open=3)]),
    ROPlan(Stage.PARTS_ORDERED, INS, NORTHLINE, 7, supplements=[SupplementPlan(S.SUBMITTED, days_open=2)]),
    ROPlan(Stage.PARTS_ORDERED, CP, None, 6),
    ROPlan(Stage.PARTS_RECEIVED, INS, NORTHLINE, 9, supplements=[SupplementPlan(S.SUBMITTED, days_open=1)]),
    ROPlan(Stage.PARTS_RECEIVED, INS, VALLEY, 10),
    ROPlan(Stage.BODY_REPAIR, INS, HARBOR, 10, supplements=[SupplementPlan(S.SUBMITTED, days_open=0)]),
    ROPlan(Stage.BODY_REPAIR, INS, NORTHLINE, 11, consent="out"),
    ROPlan(Stage.BODY_REPAIR, CP, None, 8),
    ROPlan(Stage.PAINT, INS, NORTHLINE, 12, scheduled=True),
    ROPlan(Stage.PAINT, INS, SUMMIT, 13),
    ROPlan(Stage.PAINT, CP, None, 9),
    ROPlan(Stage.PAINT, CP, None, 10, inbound="Can I pick it up Saturday?"),
    ROPlan(Stage.REASSEMBLY, INS, COASTAL, 14),
    ROPlan(Stage.REASSEMBLY, CP, None, 11),
    ROPlan(Stage.QUALITY_CHECK, CP, None, 12, consent="none"),
    ROPlan(Stage.READY_FOR_PICKUP, INS, NORTHLINE, 15),
    ROPlan(Stage.READY_FOR_PICKUP, CP, None, 13),
    ROPlan(Stage.ON_HOLD, INS, VALLEY, 18),
]
DELIVERED = [
    ROPlan(Stage.DELIVERED, INS, NORTHLINE, 3, supplements=[SupplementPlan(S.APPROVED, decision_days=1)]),
    ROPlan(
        Stage.DELIVERED,
        INS,
        NORTHLINE,
        9,
        supplements=[SupplementPlan(S.APPROVED, decision_days=2), SupplementPlan(S.PARTIALLY_APPROVED, decision_days=1)],
    ),
    ROPlan(Stage.DELIVERED, INS, NORTHLINE, 16, supplements=[SupplementPlan(S.APPROVED, decision_days=2)]),
    ROPlan(Stage.DELIVERED, INS, NORTHLINE, 24),
    ROPlan(Stage.DELIVERED, INS, NORTHLINE, 33),
    ROPlan(Stage.DELIVERED, INS, NORTHLINE, 45),
    ROPlan(Stage.DELIVERED, INS, HARBOR, 6, supplements=[SupplementPlan(S.APPROVED, decision_days=4)]),
    ROPlan(Stage.DELIVERED, INS, HARBOR, 20, supplements=[SupplementPlan(S.PARTIALLY_APPROVED, decision_days=5)]),
    ROPlan(Stage.DELIVERED, INS, HARBOR, 38, supplements=[SupplementPlan(S.DENIED, decision_days=5)]),
    ROPlan(Stage.DELIVERED, INS, SUMMIT, 12, supplements=[SupplementPlan(S.APPROVED, decision_days=3)]),
    ROPlan(Stage.DELIVERED, INS, COASTAL, 28, supplements=[SupplementPlan(S.APPROVED, decision_days=3)]),
    ROPlan(Stage.DELIVERED, INS, VALLEY, 50, consent="none"),
    ROPlan(Stage.DELIVERED, CP, None, 5),
    ROPlan(Stage.DELIVERED, CP, None, 18),
    ROPlan(Stage.DELIVERED, CP, None, 41, consent="none"),
]

INSURANCE_PATH = [
    Stage.CHECKED_IN, Stage.WAITING_ON_INSURANCE, Stage.TEARDOWN, Stage.SUPPLEMENT_PENDING, Stage.PARTS_ORDERED,
    Stage.PARTS_RECEIVED, Stage.BODY_REPAIR, Stage.PAINT, Stage.REASSEMBLY, Stage.QUALITY_CHECK,
    Stage.READY_FOR_PICKUP, Stage.DELIVERED,
]  # fmt: skip


def stage_path(plan: ROPlan) -> list[Stage]:
    """The stages an RO went through to reach its current stage."""
    path = [s for s in INSURANCE_PATH if plan.payer == INS or s not in (Stage.WAITING_ON_INSURANCE, Stage.SUPPLEMENT_PENDING)]
    if not plan.supplements or plan.supplements[0].status == S.DRAFT:
        path = [s for s in path if s != Stage.SUPPLEMENT_PENDING]
    if plan.stage == Stage.ON_HOLD:
        return path[: path.index(Stage.PARTS_ORDERED) + 1] + [Stage.ON_HOLD]
    return path[: path.index(plan.stage) + 1]


def business_days_back(today: dt.date, n: int) -> dt.date:
    """A weekday d such that business_days_between(d, today) == n."""
    day = today
    counted = 0
    while counted < n:
        if is_business_day(day):
            counted += 1
        day -= dt.timedelta(days=1)
    while not is_business_day(day):
        day -= dt.timedelta(days=1)
    return day


class DemoBuilder:
    def __init__(self, db: Session, now: dt.datetime):
        self.db = db
        self.now = now
        self.rng = random.Random(42)
        self.today = to_local(now, TZ).date()

    # -------------------------------------------------------- helpers

    def at(self, day: dt.date, hour: int, minute: int = 0) -> dt.datetime:
        """Local wall-clock time on `day`; minutes past 59 roll into the following hours."""
        wall = dt.datetime.combine(day, dt.time(0, 0)) + dt.timedelta(hours=hour, minutes=minute)
        return local_to_utc(wall, TZ)

    def work_time(self, day: dt.date) -> dt.datetime:
        """A random time between 09:00 and 15:59 local."""
        minutes = self.rng.randrange(0, 7 * 60)
        return self.at(day, 9 + minutes // 60, minutes % 60)

    def demo_id(self) -> str:
        return f"demo-{uuid.UUID(int=self.rng.getrandbits(128), version=4)}"

    def user(self) -> User:
        return self.rng.choice(self.users)

    def money(self, low_dollars: int, high_dollars: int) -> int:
        return self.rng.randrange(low_dollars * 100, high_dollars * 100, 50)

    # -------------------------------------------------------- build

    def build(self) -> None:
        db = self.db
        self.shop = Shop(name=DEMO_SHOP_NAME, phone_e164="+18185550100", timezone=TZ, address="1200 Brand Blvd, Glendale, CA (Demo)")
        db.add(self.shop)
        db.flush()
        self.settings = ShopSettings(shop_id=self.shop.id, messaging_mode=MessagingMode.DEMO, review_url="https://example.com/review")
        db.add(self.settings)
        db.flush()  # fills in the Section 13 defaults

        password_hash = hash_password(DEMO_PASSWORD)
        self.admin = User(shop_id=self.shop.id, email="admin@demo.local", password_hash=password_hash, full_name="Demo Admin", role=Role.ADMIN)
        staff1 = User(shop_id=self.shop.id, email="staff1@demo.local", password_hash=password_hash, full_name="Sam Rivera", role=Role.STAFF)
        staff2 = User(shop_id=self.shop.id, email="staff2@demo.local", password_hash=password_hash, full_name="Lena Park", role=Role.STAFF)
        db.add_all([self.admin, staff1, staff2])
        db.flush()
        self.users = [self.admin, staff1, staff2]

        self.insurers: dict[str, Insurer] = {}
        self.adjusters: dict[str, list[Adjuster]] = {}
        names = iter(ADJUSTER_NAMES)
        for name, drp, interval in INSURERS:
            insurer = Insurer(shop_id=self.shop.id, name=name, is_drp=drp, follow_up_interval_business_days=interval)
            db.add(insurer)
            db.flush()
            self.insurers[name] = insurer
            self.adjusters[name] = []
            for _ in range(2):
                full_name = next(names)
                first, last = full_name.lower().split()
                adjuster = Adjuster(
                    shop_id=self.shop.id, insurer_id=insurer.id, full_name=full_name, email=f"{first}.{last}@{CLAIM_PREFIX[name].lower()}-demo.example"
                )
                db.add(adjuster)
                self.adjusters[name].append(adjuster)
        db.flush()

        plans = [(plan, self.schedule(plan)) for plan in ACTIVE + DELIVERED]
        plans.sort(key=lambda item: item[1][0])  # by check-in time, so RO numbers follow check-in order
        first_names = self.rng.sample(FIRST_NAMES, len(plans))
        for index, (plan, times) in enumerate(plans):
            self.build_ro(index, plan, times, first_names[index])
        db.flush()
        self.check_revenue_share()
        db.commit()

    def schedule(self, plan: ROPlan) -> list[dt.datetime]:
        """Times for every stage event: check-in first, each transition on its own day where possible."""
        path = stage_path(plan)
        transitions = len(path) - 1
        if plan.stage == Stage.DELIVERED:
            cycle = transitions + self.rng.randint(1, 8)
            delivered_day = self.today - dt.timedelta(days=plan.days_ago)
            first_day = delivered_day - dt.timedelta(days=cycle)
            last_day = delivered_day
        else:
            first_day = self.today - dt.timedelta(days=plan.days_ago)
            last_day = self.today - dt.timedelta(days=1)
        times = [self.at(first_day, 8, self.rng.randrange(0, 60))]
        span = (last_day - first_day).days
        regular = transitions - 1 if plan.scheduled else transitions
        for j in range(1, regular + 1):
            day = first_day + dt.timedelta(days=max(1, (j * span) // max(regular, 1)))
            times.append(max(self.work_time(day), times[-1] + dt.timedelta(minutes=30)))
        if plan.scheduled:
            times.append(self.now - dt.timedelta(minutes=5))
        return times

    def build_ro(self, index: int, plan: ROPlan, times: list[dt.datetime], first_name: str) -> None:
        db = self.db
        path = stage_path(plan)
        phone = f"+1818555{101 + index:04d}"
        last_name = self.rng.choice(LAST_NAMES)
        email = f"{first_name.lower()}.{last_name.lower()}@example.com" if self.rng.random() < 0.5 else None
        customer = Customer(shop_id=self.shop.id, first_name=first_name, last_name=last_name, phone_e164=phone, email=email)
        db.add(customer)
        db.flush()

        make, model = self.rng.choice(VEHICLES)
        insurer = self.insurers[plan.insurer] if plan.insurer else None
        adjuster = self.rng.choice(self.adjusters[plan.insurer]) if insurer else None
        if plan.insurer == NORTHLINE and plan.stage == Stage.DELIVERED:
            estimate = self.money(8000, 12000)
        else:
            estimate = self.money(1500, 5000)
        ro = RepairOrder(
            shop_id=self.shop.id,
            ro_number=f"24-{1201 + index}",
            customer_id=customer.id,
            vehicle_year=self.rng.randint(2012, 2025),
            vehicle_make=make,
            vehicle_model=model,
            vehicle_color=self.rng.choice(COLORS),
            payer_type=plan.payer,
            insurer_id=insurer.id if insurer else None,
            adjuster_id=adjuster.id if adjuster else None,
            claim_number=f"{CLAIM_PREFIX[plan.insurer]}-{self.rng.randint(10000, 99999)}" if insurer else None,
            original_estimate_cents=estimate,
            current_stage=plan.stage,
            checked_in_at=times[0],
            needs_reply=plan.inbound is not None,
        )
        db.add(ro)
        db.flush()

        opt_out_at = None
        if plan.consent in ("in", "out"):
            db.add(
                Consent(
                    shop_id=self.shop.id,
                    customer_id=customer.id,
                    phone_e164=phone,
                    status=ConsentStatus.OPTED_IN,
                    method=ConsentMethod.IN_PERSON_VERBAL,
                    recorded_by_user_id=self.user().id,
                    recorded_at=times[0],
                )
            )
        if plan.consent == "out":
            parts_ordered = times[path.index(Stage.PARTS_ORDERED)]
            opt_out_at = parts_ordered + (times[path.index(Stage.PARTS_ORDERED) + 1] - parts_ordered) / 2

        approved_total = self.build_supplements(ro, plan, path, times)

        for i, (stage, when) in enumerate(zip(path, times)):
            if opt_out_at is not None and when > opt_out_at and (i == 0 or times[i - 1] <= opt_out_at):
                self.opt_out(ro, customer, opt_out_at)
            db.add(
                StageEvent(
                    shop_id=self.shop.id,
                    repair_order_id=ro.id,
                    from_stage=path[i - 1] if i else None,
                    to_stage=stage,
                    changed_by_user_id=self.user().id,
                    changed_at=when,
                    created_at=when,
                    updated_at=when,
                )
            )
            if stage == Stage.DELIVERED:
                ro.final_invoice_cents = estimate + approved_total + (self.money(0, 300) if plan.payer == CP else 0)
                ro.delivered_at = when
            db.flush()
            if DEFAULT_TEXT_ENABLED[stage]:
                self.stage_text(ro, customer, plan, stage, when, opted_out=opt_out_at is not None and when > opt_out_at)

        if plan.inbound:
            received = self.now - (dt.timedelta(hours=3) if plan.stage == Stage.WAITING_ON_INSURANCE else dt.timedelta(minutes=40))
            db.add(
                Message(
                    shop_id=self.shop.id,
                    repair_order_id=ro.id,
                    customer_id=customer.id,
                    direction=MessageDirection.INBOUND,
                    kind=MessageKind.INBOUND_REPLY,
                    status=MessageStatus.RECEIVED,
                    to_e164=self.shop.phone_e164,
                    from_e164=phone,
                    body=plan.inbound,
                    created_at=received,
                    updated_at=received,
                )
            )
        db.flush()

    def stage_text(self, ro: RepairOrder, customer: Customer, plan: ROPlan, stage: Stage, when: dt.datetime, opted_out: bool) -> None:
        body = render_stage_text(self.db, self.shop, self.settings, ro, stage_template(self.settings, stage))
        send_at = when + dt.timedelta(minutes=self.settings.cool_off_minutes)
        message = Message(
            shop_id=self.shop.id,
            repair_order_id=ro.id,
            customer_id=customer.id,
            direction=MessageDirection.OUTBOUND,
            kind=MessageKind.STAGE_UPDATE,
            to_e164=customer.phone_e164,
            from_e164=self.shop.phone_e164,
            body=body,
            stage=stage,
            scheduled_send_at=send_at,
            created_at=when,
            updated_at=when,
        )
        if plan.scheduled and stage == plan.stage:
            message.status = MessageStatus.SCHEDULED
        elif plan.consent == "none":
            message.status = MessageStatus.BLOCKED_NO_CONSENT
        elif opted_out:
            message.status = MessageStatus.BLOCKED_OPTED_OUT
        else:
            message.status = MessageStatus.DELIVERED
            message.sent_at = send_at
            message.provider_message_id = self.demo_id()
        self.db.add(message)
        self.db.flush()

    def opt_out(self, ro: RepairOrder, customer: Customer, when: dt.datetime) -> None:
        """The customer texted STOP: keyword stored, consent OPTED_OUT, confirmation delivered."""
        db = self.db
        common = {"shop_id": self.shop.id, "repair_order_id": ro.id, "customer_id": customer.id}
        db.add(
            Message(
                **common,
                direction=MessageDirection.INBOUND,
                kind=MessageKind.INBOUND_KEYWORD,
                status=MessageStatus.RECEIVED,
                to_e164=self.shop.phone_e164,
                from_e164=customer.phone_e164,
                body="STOP",
                created_at=when,
                updated_at=when,
            )
        )
        db.add(
            Consent(
                shop_id=self.shop.id,
                customer_id=customer.id,
                phone_e164=customer.phone_e164,
                status=ConsentStatus.OPTED_OUT,
                method=ConsentMethod.KEYWORD,
                recorded_by_user_id=None,
                recorded_at=when,
            )
        )
        reply = replace_variables(
            OPT_OUT_REPLY,
            template_variables(first_name="", shop_name=self.shop.name, shop_phone_e164=self.shop.phone_e164, vehicle="", ro_number="", review_url=""),
        )
        db.add(
            Message(
                **common,
                direction=MessageDirection.OUTBOUND,
                kind=MessageKind.OPT_OUT_CONFIRMATION,
                status=MessageStatus.DELIVERED,
                to_e164=customer.phone_e164,
                from_e164=self.shop.phone_e164,
                body=reply,
                scheduled_send_at=when,
                sent_at=when,
                provider_message_id=self.demo_id(),
                created_at=when,
                updated_at=when,
            )
        )
        db.flush()

    # -------------------------------------------------------- supplements

    def build_supplements(self, ro: RepairOrder, plan: ROPlan, path: list[Stage], times: list[dt.datetime]) -> int:
        """Returns the approved total in cents."""
        approved_total = 0
        anchor = times[path.index(Stage.SUPPLEMENT_PENDING)] if Stage.SUPPLEMENT_PENDING in path else times[-1]
        for seq, spec in enumerate(plan.supplements, start=1):
            requested = self.money(500, 2000) if plan.stage == Stage.DELIVERED else self.money(400, 3500)
            supplement = Supplement(
                shop_id=self.shop.id,
                repair_order_id=ro.id,
                sequence_number=seq,
                status=spec.status,
                description=self.rng.choice(SUPPLEMENT_DESCRIPTIONS),
                requested_cents=requested,
                approved_cents=0,
                adjuster_id=ro.adjuster_id,
                follow_up_count=0,
            )
            self.db.add(supplement)
            self.db.flush()
            if spec.status == S.DRAFT:
                created = max(anchor + dt.timedelta(hours=1), self.now - dt.timedelta(hours=2))
                self.supplement_event(supplement, None, S.DRAFT, min(created, self.now))
                continue

            if spec.status == S.SUBMITTED:
                submitted = self.submitted_time(spec.days_open)
            else:
                submitted = anchor + dt.timedelta(minutes=30 + 60 * (seq - 1))
            supplement.submitted_at = submitted
            self.supplement_event(supplement, None, S.DRAFT, submitted - dt.timedelta(hours=1))
            self.supplement_event(supplement, S.DRAFT, S.SUBMITTED, submitted)
            interval = self.insurers[plan.insurer].follow_up_interval_business_days or self.settings.default_follow_up_interval_business_days
            if spec.status == S.SUBMITTED:
                self.follow_ups(supplement, spec, interval)
            else:
                decided_day = add_business_days(to_local(submitted, TZ).date(), spec.decision_days)
                decided = self.at(decided_day, 11, self.rng.randrange(0, 240))
                supplement.decided_at = decided
                if spec.status == S.APPROVED:
                    supplement.approved_cents = requested
                elif spec.status == S.PARTIALLY_APPROVED:
                    supplement.approved_cents = (requested * 6 // 10) // 100 * 100
                approved_total += supplement.approved_cents
                self.supplement_event(supplement, S.SUBMITTED, spec.status, decided)
        self.db.flush()
        return approved_total

    def submitted_time(self, days_open: int) -> dt.datetime:
        """Counting business days backward from today at 10:00 local; never in the future."""
        if days_open == 0:
            return min(self.at(self.today, 10), self.now)
        return self.at(business_days_back(self.today, days_open), 10)

    def follow_ups(self, supplement: Supplement, spec: SupplementPlan, interval: int) -> None:
        submitted_day = to_local(supplement.submitted_at, TZ).date()
        due_time = self.settings.follow_up_due_time
        if spec.due:
            # Follow-ups logged every `interval` business days, leaving the next one due before today.
            last_day = submitted_day
            while True:
                candidate = add_business_days(last_day, interval)
                if add_business_days(candidate, interval) >= self.today:
                    break
                last_day = candidate
                self.log_follow_up(supplement, self.at(candidate, 11, self.rng.randrange(0, 120)))
            supplement.next_follow_up_due_at = local_datetime_at(add_business_days(last_day, interval), due_time, TZ)
            return
        natural_due = local_datetime_at(add_business_days(submitted_day, interval), due_time, TZ)
        if natural_due <= self.now:
            # Someone already called today, so it is not due again yet.
            when = max(self.now - dt.timedelta(minutes=5), supplement.submitted_at + dt.timedelta(minutes=1))
            self.log_follow_up(supplement, when)
            supplement.next_follow_up_due_at = local_datetime_at(add_business_days(to_local(when, TZ).date(), interval), due_time, TZ)
        else:
            supplement.next_follow_up_due_at = natural_due

    def log_follow_up(self, supplement: Supplement, when: dt.datetime) -> None:
        supplement.follow_up_count += 1
        supplement.last_follow_up_at = when
        method = self.rng.choice([FollowUpMethod.PHONE, FollowUpMethod.EMAIL, FollowUpMethod.INSURER_PORTAL])
        self.db.add(
            SupplementEvent(
                shop_id=self.shop.id,
                supplement_id=supplement.id,
                event_type=SupplementEventType.FOLLOW_UP,
                follow_up_method=method,
                note="Left a message for the adjuster" if method == FollowUpMethod.PHONE else None,
                user_id=self.user().id,
                occurred_at=when,
                created_at=when,
                updated_at=when,
            )
        )

    def supplement_event(self, supplement: Supplement, from_status, to_status, when: dt.datetime) -> None:
        self.db.add(
            SupplementEvent(
                shop_id=self.shop.id,
                supplement_id=supplement.id,
                event_type=SupplementEventType.STATUS_CHANGE,
                from_status=from_status,
                to_status=to_status,
                user_id=self.user().id,
                occurred_at=when,
                created_at=when,
                updated_at=when,
            )
        )

    def check_revenue_share(self) -> None:
        delivered = self.db.scalars(select(RepairOrder).where(RepairOrder.shop_id == self.shop.id, RepairOrder.delivered_at.is_not(None))).all()
        total = sum(ro.final_invoice_cents for ro in delivered)
        northline = sum(ro.final_invoice_cents for ro in delivered if ro.insurer_id == self.insurers[NORTHLINE].id)
        if northline * 100 <= total * 40:
            raise RuntimeError("Demo data error: Northline must be over 40% of delivered revenue.")


def build_demo(db: Session, now: dt.datetime | None = None) -> Shop:
    builder = DemoBuilder(db, now or utcnow())
    builder.build()
    return builder.shop


def create_shop(
    db: Session,
    *,
    name: str,
    phone: str,
    timezone: str,
    admin_email: str,
    admin_name: str,
    admin_password: str,
    address: str | None = None,
) -> Shop:
    """A real (non-demo) shop in DEMO mode, its settings row and its first ADMIN user. Raises ValueError."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    from app.auth import validate_new_password
    from app.messaging.templates import parse_us_phone

    name = (name or "").strip()
    if not 1 <= len(name) <= 120:
        raise ValueError("Shop name must be 1 to 120 characters.")
    if db.scalar(select(Shop.id).where(Shop.name == name)) is not None:
        raise ValueError(f"A shop named {name} already exists.")
    phone_e164 = parse_us_phone(phone)
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"Unknown time zone: {timezone}") from None
    address = (address or "").strip() or None
    if address and len(address) > 200:
        raise ValueError("Address must be at most 200 characters.")
    admin_email = (admin_email or "").strip().lower()
    if "@" not in admin_email or len(admin_email) > 254:
        raise ValueError("Enter a valid admin email.")
    if db.scalar(select(User.id).where(User.email == admin_email)) is not None:
        raise ValueError(f"A user with email {admin_email} already exists.")
    admin_name = (admin_name or "").strip()
    if not 1 <= len(admin_name) <= 120:
        raise ValueError("Admin name must be 1 to 120 characters.")
    validate_new_password(admin_password or "")

    shop = Shop(name=name, phone_e164=phone_e164, timezone=timezone, address=address)
    db.add(shop)
    db.flush()
    db.add(ShopSettings(shop_id=shop.id, messaging_mode=MessagingMode.DEMO))
    db.add(User(shop_id=shop.id, email=admin_email, password_hash=hash_password(admin_password), full_name=admin_name, role=Role.ADMIN))
    db.commit()
    return shop


def _new_shop_password() -> str:
    import getpass
    import os

    password = os.environ.get("NEW_ADMIN_PASSWORD")
    if password:
        return password
    first = getpass.getpass("Password for the first admin (12+ characters): ")
    if getpass.getpass("Type it again: ") != first:
        raise ValueError("The passwords do not match.")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.seed", description="Load demo data, or create a real shop.")
    parser.add_argument("--demo", action="store_true", help="build the demo shop Brand Blvd Collision (Demo)")
    parser.add_argument("--new-shop", action="store_true", help="create a real shop and its first admin (password from NEW_ADMIN_PASSWORD or a prompt)")
    parser.add_argument("--name", help="shop name, shown in every text")
    parser.add_argument("--phone", help="shop phone, any US format")
    parser.add_argument("--timezone", default=TZ, help=f"IANA time zone (default {TZ})")
    parser.add_argument("--address", default=None)
    parser.add_argument("--admin-email")
    parser.add_argument("--admin-name")
    args = parser.parse_args(argv)
    if args.demo == args.new_shop:
        parser.print_help()
        return 2
    if args.new_shop:
        missing = [flag for flag, value in (("--name", args.name), ("--phone", args.phone), ("--admin-email", args.admin_email), ("--admin-name", args.admin_name)) if not value]
        if missing:
            print(f"Missing: {', '.join(missing)}", file=sys.stderr)
            return 2

    settings = Settings()
    engine = make_engine(settings.DATABASE_URL)
    session_factory = make_session_factory(engine)
    try:
        with session_factory() as db:
            if args.new_shop:
                try:
                    shop = create_shop(
                        db,
                        name=args.name,
                        phone=args.phone,
                        timezone=args.timezone,
                        address=args.address,
                        admin_email=args.admin_email,
                        admin_name=args.admin_name,
                        admin_password=_new_shop_password(),
                    )
                except ValueError as exc:
                    print(f"Not created: {exc}", file=sys.stderr)
                    return 1
                print(f"Created {shop.name} in DEMO mode. {args.admin_email.strip().lower()} can now log in as ADMIN.")
                print("Next: Settings > Shop and Texting, then Settings > Mode when you are ready for LIVE.")
                return 0
            if db.scalar(select(Shop.id).where(Shop.name == DEMO_SHOP_NAME)) is not None:
                print("Demo shop already exists.")
                return 1
            build_demo(db)
    except OperationalError as exc:
        print(f"Database error: {exc.orig}. Run `alembic upgrade head` first.", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    print(f"Created {DEMO_SHOP_NAME} in DEMO mode.")
    print("Log in at http://localhost:8000/login with any of these (password for all: demo-password-123):")
    print("  admin@demo.local   ADMIN  Demo Admin")
    print("  staff1@demo.local  STAFF  Sam Rivera")
    print("  staff2@demo.local  STAFF  Lena Park")
    return 0


if __name__ == "__main__":
    sys.exit(main())
