"""Deterministic helpers: validate, qualify, match, escalate."""

from __future__ import annotations

from enum import StrEnum

from app.entities.enums import CaseStatus, EscalationReason, SupplierCandidateStatus
from app.entities.models import Case, Escalation, Order, SupplierCandidate, SupplierFacts
from app.policies import (
    MEDICARE_PART_B_REQUIRED,
    REQUIRE_FACE_TO_FACE,
    REQUIRE_HOME_ASSESSMENT,
    REQUIRES_ACCEPTS_ASSIGNMENT,
    WRITTEN_ORDER_REQUIRED,
    assess_patient_eligibility,
    expected_hcpcs,
)


class QualifyResult(StrEnum):
    QUALIFIED = "QUALIFIED"
    REJECTED = "REJECTED"
    RETRYABLE = "RETRYABLE"


def validate_order(order: Order, equipment: str) -> tuple[bool, list[str]]:
    """PCP/SWO paperwork gates (signed written order + supporting docs)."""
    errors: list[str] = []
    if WRITTEN_ORDER_REQUIRED and not order.signed:
        errors.append("written order signature required")
    if REQUIRE_FACE_TO_FACE and not order.face_to_face:
        errors.append("face-to-face evaluation required")
    if REQUIRE_HOME_ASSESSMENT and not order.home_assessment:
        errors.append("home assessment required")
    for value, label in [
        (order.patient_name, "patient name"),
        (order.order_date, "order date"),
        (order.item_description, "item description"),
        (order.practitioner_name, "practitioner name"),
    ]:
        if not value:
            errors.append(f"{label} required")

    expected = expected_hcpcs(equipment)
    if expected is None:
        errors.append(f"unsupported equipment: {equipment}")
    else:
        item = (order.item_description or "").lower()
        code = (order.hcpcs_code or "").upper()
        if expected.lower() not in item and "wheelchair" not in item and code != expected:
            errors.append(f"equipment/code mismatch; expected {expected}")
        if code and code != expected:
            errors.append(f"HCPCS must be {expected}")
        if not code:
            order.hcpcs_code = expected
    return len(errors) == 0, errors


def qualify_supplier(facts: SupplierFacts) -> QualifyResult:
    """Supplier-call facts vs Medicare enrollment / assignment / stock rules."""
    if facts.no_answer or facts.out_of_stock or facts.callback_requested:
        return QualifyResult.RETRYABLE
    if facts.accepting_new_patients is False:
        return QualifyResult.REJECTED
    if facts.k0001_available is False and not facts.out_of_stock:
        return QualifyResult.REJECTED
    if MEDICARE_PART_B_REQUIRED and facts.medicare_part_b is False:
        return QualifyResult.REJECTED
    if REQUIRES_ACCEPTS_ASSIGNMENT and facts.accepts_assignment is False:
        return QualifyResult.REJECTED
    if (
        facts.accepting_new_patients is True
        and (not MEDICARE_PART_B_REQUIRED or facts.medicare_part_b is True)
        and facts.k0001_available is True
        and facts.delivery_possible is True
        and (not REQUIRES_ACCEPTS_ASSIGNMENT or facts.accepts_assignment is True)
    ):
        return QualifyResult.QUALIFIED
    if any(
        v is None
        for v in (
            facts.accepting_new_patients,
            facts.medicare_part_b,
            facts.k0001_available,
            facts.delivery_possible,
            facts.accepts_assignment if REQUIRES_ACCEPTS_ASSIGNMENT else True,
        )
    ):
        return QualifyResult.RETRYABLE
    return QualifyResult.REJECTED


def can_match(case: Case) -> SupplierCandidate | None:
    if not case.order_validated or not case.order or not case.order.is_valid:
        return None
    ok, _ = assess_patient_eligibility(case.eligibility)
    if not ok:
        return None
    for c in case.candidates:
        if c.status == SupplierCandidateStatus.QUALIFIED:
            return c
    return None


def is_overdue(case: Case) -> bool:
    if not case.commitment or case.commitment.confirmed_at or case.commitment.breached:
        return False
    return bool(case.scenario_clock and case.scenario_clock > case.commitment.commitment_date)


def build_escalation(
    case: Case,
    reason: EscalationReason,
    *,
    attempts: int,
    recommended_action: str,
) -> Escalation:
    return Escalation(
        reason=reason,
        attempts=attempts,
        recommended_action=recommended_action,
        current_state=case.status if case.status != CaseStatus.ESCALATED else CaseStatus.ESCALATED,
    )
