"""
services/risk_engine.py
=======================
PayNPass Risk Intelligence Engine — deterministic rule-based scorer.

Design principles
─────────────────
• Every rule is an independent function → easy to add / disable / tune.
• Each rule returns a RuleResult(points, triggered, reason).
• The engine accumulates points, caps at 100, assigns a level, and
  writes the updated score back to the session row.
• Past inspection history (MISMATCH) for the same user raises base risk.

Risk levels
───────────
  0 – 30  → LOW    (GREEN)
  31 – 60 → MEDIUM (YELLOW)
  61+     → HIGH   (RED)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app.models.inspection import Inspection
from app.models.session import Event, Session

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Tuneable thresholds  (change here, not inside rules)
# ─────────────────────────────────────────────────────────────────────────────
THRESHOLDS = {
    # Rule 1 – lingering without buying
    "long_stay_minutes": 25,
    "long_stay_low_cart_value": Decimal("200.00"),
    "long_stay_points": 15,

    # Rule 2 – excessive cart edits
    "high_edit_count": 6,
    "high_edit_points": 10,

    # Rule 3 – repeated item removals
    "removal_count_threshold": 3,
    "removal_points": 10,

    # Rule 4 – payment much lower than scanned value
    "payment_gap_ratio": Decimal("0.70"),   # paid < 70 % of cart value
    "payment_gap_min_cart": Decimal("100.00"),  # only trigger if cart was meaningful
    "payment_gap_points": 20,

    # Rule 5 – repeat offender (prior MISMATCH inspections)
    "repeat_mismatch_threshold": 1,
    "repeat_mismatch_points": 20,

    # Rule 6 – scanned items, no checkout after long time
    "no_checkout_minutes": 30,
    "no_checkout_min_items": 1,
    "no_checkout_points": 15,

    # Rule 7 – rapid scan–remove pattern (scan then immediately remove)
    "scan_remove_rapid_threshold": 4,   # N items rapidly removed
    "scan_remove_rapid_points": 10,

    # Rule 8 – very high cart value paid in full is a good signal (negative score adj)
    # (no negative scoring in v1 — reserved for v2)
}

MAX_SCORE = 100


# ─────────────────────────────────────────────────────────────────────────────
# Data transfer objects
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RuleResult:
    rule_id: str
    triggered: bool
    points: int
    reason: str


@dataclass
class RiskReport:
    session_id: int
    score: int
    level: str          # LOW / MEDIUM / HIGH
    flagged: bool
    rules_triggered: List[RuleResult] = field(default_factory=list)
    rules_evaluated: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────

def _score_to_level(score: int) -> str:
    if score <= 30:
        return "LOW"
    if score <= 60:
        return "MEDIUM"
    return "HIGH"


def _elapsed_minutes(session: Session) -> Optional[float]:
    """Minutes from session start to now (or end_time if completed)."""
    reference = session.end_time or datetime.now(timezone.utc)
    start = session.start_time
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return (reference - start).total_seconds() / 60


# ─────────────────────────────────────────────────────────────────────────────
# Individual rules
# ─────────────────────────────────────────────────────────────────────────────

def rule_long_stay_low_cart(session: Session) -> RuleResult:
    """
    R01 – Customer has been in the store > 25 min but cart value < ₹200.
    Possible sign of loitering or scanning-and-removing to mask theft.
    """
    t = THRESHOLDS
    elapsed = _elapsed_minutes(session) or 0
    cart = session.cart_value or Decimal("0")

    triggered = (
        elapsed > t["long_stay_minutes"]
        and cart < t["long_stay_low_cart_value"]
    )
    return RuleResult(
        rule_id="R01",
        triggered=triggered,
        points=t["long_stay_points"] if triggered else 0,
        reason=(
            f"In store {elapsed:.1f} min with cart ₹{cart:.2f} "
            f"(threshold: >{t['long_stay_minutes']} min & <₹{t['long_stay_low_cart_value']})"
        ),
    )


def rule_high_cart_edits(session: Session) -> RuleResult:
    """
    R02 – Cart has been edited (item added/removed/qty-changed) > 6 times.
    Normal shoppers rarely need this many changes.
    """
    t = THRESHOLDS
    edits = session.cart_edit_count or 0
    triggered = edits > t["high_edit_count"]
    return RuleResult(
        rule_id="R02",
        triggered=triggered,
        points=t["high_edit_points"] if triggered else 0,
        reason=f"Cart edited {edits} times (threshold: >{t['high_edit_count']})",
    )


def rule_repeated_removals(session: Session) -> RuleResult:
    """
    R03 – More than 3 distinct item removals in the session.
    Repeatedly scanning and removing items is a red flag.
    """
    t = THRESHOLDS
    removals = session.total_items_removed or 0
    triggered = removals > t["removal_count_threshold"]
    return RuleResult(
        rule_id="R03",
        triggered=triggered,
        points=t["removal_points"] if triggered else 0,
        reason=f"{removals} item removals (threshold: >{t['removal_count_threshold']})",
    )


def rule_payment_gap(session: Session) -> RuleResult:
    """
    R04 – Payment amount is significantly lower than the scanned cart value.
    Indicates items may have been scanned but not paid for.
    Only evaluated after PAYMENT_SUCCESS.
    """
    t = THRESHOLDS
    cart = session.cart_value or Decimal("0")
    paid = session.payment_amount

    if paid is None or cart < t["payment_gap_min_cart"]:
        return RuleResult(
            rule_id="R04",
            triggered=False,
            points=0,
            reason="Payment not yet recorded or cart too small to evaluate",
        )

    ratio = paid / cart if cart > 0 else Decimal("1")
    triggered = ratio < t["payment_gap_ratio"]
    return RuleResult(
        rule_id="R04",
        triggered=triggered,
        points=t["payment_gap_points"] if triggered else 0,
        reason=(
            f"Paid ₹{paid:.2f} vs cart ₹{cart:.2f} "
            f"({float(ratio)*100:.1f}% — threshold: >{float(t['payment_gap_ratio'])*100:.0f}%)"
        ),
    )


def rule_repeat_offender(session: Session, db: DBSession) -> RuleResult:
    """
    R05 – User has ≥ 1 prior MISMATCH inspection on record.
    Historical fraud signal carries forward.
    """
    t = THRESHOLDS
    prior_mismatches = (
        db.query(func.count(Inspection.id))
        .join(Session, Inspection.session_id == Session.id)
        .filter(
            Session.user_id == session.user_id,
            Session.id != session.id,
            Inspection.inspection_result == "MISMATCH",
        )
        .scalar()
    ) or 0

    triggered = prior_mismatches >= t["repeat_mismatch_threshold"]
    return RuleResult(
        rule_id="R05",
        triggered=triggered,
        points=t["repeat_mismatch_points"] if triggered else 0,
        reason=f"User has {prior_mismatches} prior MISMATCH inspection(s)",
    )


def rule_no_checkout_long_time(session: Session) -> RuleResult:
    """
    R06 – Session has scanned items but CHECKOUT_STARTED has not been received
    after more than 30 minutes. Possibly lingering after filling a bag.
    Only evaluated on active sessions (end_time is None).
    """
    t = THRESHOLDS
    if session.end_time is not None:
        # Session is complete; rule no longer relevant
        return RuleResult(
            rule_id="R06", triggered=False, points=0,
            reason="Session already completed",
        )

    elapsed = _elapsed_minutes(session) or 0
    items = session.total_items_scanned or 0
    triggered = (
        elapsed > t["no_checkout_minutes"]
        and items >= t["no_checkout_min_items"]
    )
    return RuleResult(
        rule_id="R06",
        triggered=triggered,
        points=t["no_checkout_points"] if triggered else 0,
        reason=(
            f"{items} item(s) scanned, no checkout after {elapsed:.1f} min "
            f"(threshold: >{t['no_checkout_minutes']} min)"
        ),
    )


def rule_rapid_scan_remove_cycle(session: Session, db: DBSession) -> RuleResult:
    """
    R07 – Customer has performed ≥ N scan-then-immediate-remove cycles.
    Detected by counting PRODUCT_REMOVE events that occurred within
    60 seconds of a preceding PRODUCT_SCAN for the same product.
    """
    t = THRESHOLDS
    events: list[Event] = (
        db.query(Event)
        .filter(
            Event.session_id == session.id,
            Event.event_type.in_(["PRODUCT_SCAN", "PRODUCT_SEARCH_ADD", "PRODUCT_REMOVE"]),
        )
        .order_by(Event.timestamp)
        .all()
    )

    # Build a map: product_id → list of scan timestamps
    last_scan: dict = {}
    rapid_remove_count = 0
    window_seconds = 60

    for ev in events:
        if ev.event_type in ("PRODUCT_SCAN", "PRODUCT_SEARCH_ADD"):
            last_scan[ev.product_id] = ev.timestamp
        elif ev.event_type == "PRODUCT_REMOVE" and ev.product_id in last_scan:
            scan_ts = last_scan[ev.product_id]
            remove_ts = ev.timestamp
            if scan_ts.tzinfo is None:
                scan_ts = scan_ts.replace(tzinfo=timezone.utc)
            if remove_ts.tzinfo is None:
                remove_ts = remove_ts.replace(tzinfo=timezone.utc)
            delta = (remove_ts - scan_ts).total_seconds()
            if 0 <= delta <= window_seconds:
                rapid_remove_count += 1

    triggered = rapid_remove_count >= t["scan_remove_rapid_threshold"]
    return RuleResult(
        rule_id="R07",
        triggered=triggered,
        points=t["scan_remove_rapid_points"] if triggered else 0,
        reason=(
            f"{rapid_remove_count} rapid scan→remove cycle(s) within {window_seconds}s "
            f"(threshold: ≥{t['scan_remove_rapid_threshold']})"
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main engine entry point
# ─────────────────────────────────────────────────────────────────────────────

def calculate_risk_score(session_id: int, db: DBSession) -> RiskReport:
    """
    Run all rules against the given session, persist the updated score,
    and return a full RiskReport.

    Called:
      • After every ingested event (real-time update)
      • On-demand via GET /session/{session_id}
    """
    session: Optional[Session] = db.query(Session).filter(Session.id == session_id).first()
    if session is None:
        raise ValueError(f"Session {session_id} not found")

    # ── Evaluate every rule ──────────────────────────────────────────────────
    rules_evaluated = [
        rule_long_stay_low_cart(session),
        rule_high_cart_edits(session),
        rule_repeated_removals(session),
        rule_payment_gap(session),
        rule_repeat_offender(session, db),
        rule_no_checkout_long_time(session),
        rule_rapid_scan_remove_cycle(session, db),
    ]

    triggered = [r for r in rules_evaluated if r.triggered]
    raw_score = sum(r.points for r in triggered)
    score = min(raw_score, MAX_SCORE)
    level = _score_to_level(score)
    flagged = level == "HIGH"

    # ── Persist back to session ──────────────────────────────────────────────
    session.risk_score = score
    session.risk_level = level
    session.flagged_for_inspection = flagged
    db.commit()

    logger.info(
        "Risk score for session %d: %d (%s) | rules triggered: %s",
        session_id,
        score,
        level,
        [r.rule_id for r in triggered],
    )

    return RiskReport(
        session_id=session_id,
        score=score,
        level=level,
        flagged=flagged,
        rules_triggered=triggered,
        rules_evaluated=len(rules_evaluated),
    )
