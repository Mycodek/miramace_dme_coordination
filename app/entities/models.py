from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from app.entities.enums import (
    CaseStatus,
    EscalationReason,
    EventType,
    SupplierCandidateStatus,
)


class ExtractSchema(BaseModel):
    """Pydantic models whose JSON schema is injected into LLM extract prompts."""

    @classmethod
    def to_json(cls) -> str:
        return json.dumps(cls.model_json_schema(), indent=2)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Event(BaseModel):
    event_id: str = Field(default_factory=lambda: f"EVT-{uuid4().hex[:8]}")
    type: EventType
    actor: str
    timestamp: datetime = Field(default_factory=utc_now)
    payload: dict[str, Any] = Field(default_factory=dict)


class Patient(BaseModel):
    patient_id: str
    name: str
    city: str


class PCP(BaseModel):
    pcp_id: str
    name: str
    practice: str
    phone: str


class PatientEligibility(BaseModel):
    """Patient-side K0001 medical-necessity inputs (from chart / intake)."""

    medicare_part_b: bool = True
    weight_lbs: Optional[int] = None
    in_home_mradl_limitation: bool = True
    lesser_device_insufficient: bool = True
    can_self_propel_or_has_caregiver: bool = True
    home_accessible: bool = True
    outdoor_use_only: bool = False
    leisure_only: bool = False
    backup_device_only: bool = False


class PatientRecord(BaseModel):
    patient: Patient
    pcp: PCP
    equipment: str
    eligibility: PatientEligibility = Field(default_factory=PatientEligibility)


class Supplier(BaseModel):
    supplier_id: str
    supplier_name: str
    phone: str
    address: str


class SupplierFacts(ExtractSchema):
    accepting_new_patients: Optional[bool] = None
    medicare_part_b: Optional[bool] = None
    k0001_available: Optional[bool] = None
    delivery_possible: Optional[bool] = None
    delivery_eta_days: Optional[int] = None
    accepts_assignment: Optional[bool] = Field(
        default=None,
        description=(
            "True if the supplier accepts Medicare assignment (patient pays only "
            "the standard Part B 20% coinsurance at the approved rate; no balance billing)."
        ),
    )
    callback_requested: Optional[bool] = None
    no_answer: Optional[bool] = None
    out_of_stock: Optional[bool] = None
    confidence: Optional[float] = None


class OrderExtraction(ExtractSchema):
    order_status: Optional[str] = None
    patient_name: Optional[str] = None
    order_date: Optional[str] = None
    item_description: Optional[str] = None
    practitioner_name: Optional[str] = None
    signed: Optional[bool] = Field(
        default=None,
        description=(
            "True only for a signed Standard Written Order (SWO), "
            "not a verbal order alone."
        ),
    )
    hcpcs_code: Optional[str] = None
    expected_submission: Optional[str] = None
    no_response: Optional[bool] = None
    face_to_face: Optional[bool] = Field(
        default=None,
        description="True if a face-to-face mobility evaluation is documented.",
    )
    home_assessment: Optional[bool] = Field(
        default=None,
        description="True if home accessibility for the wheelchair is documented.",
    )


class Order(BaseModel):
    patient_name: Optional[str] = None
    order_date: Optional[str] = None
    item_description: Optional[str] = None
    practitioner_name: Optional[str] = None
    signed: bool = False
    hcpcs_code: Optional[str] = None
    face_to_face: bool = False
    home_assessment: bool = False
    is_valid: bool = False
    validation_errors: list[str] = Field(default_factory=list)


class SupplierCandidate(BaseModel):
    supplier_id: str
    supplier_name: str
    phone: str
    address: str
    status: SupplierCandidateStatus = SupplierCandidateStatus.DISCOVERED
    attempt_count: int = 0
    facts: Optional[SupplierFacts] = None


class Commitment(BaseModel):
    supplier_id: str
    commitment_date: date
    confirmed_at: Optional[datetime] = None
    breached: bool = False


class Escalation(BaseModel):
    reason: EscalationReason
    attempts: int
    recommended_action: str
    current_state: CaseStatus
    details: dict[str, Any] = Field(default_factory=dict)


class Case(BaseModel):
    case_id: str = Field(default_factory=lambda: f"CASE-{uuid4().hex[:8]}")
    status: CaseStatus = CaseStatus.CREATED
    patient: Patient
    pcp: PCP
    equipment: str
    eligibility: PatientEligibility = Field(default_factory=PatientEligibility)
    created_at: datetime = Field(default_factory=utc_now)
    order: Optional[Order] = None
    order_validated: bool = False
    candidates: list[SupplierCandidate] = Field(default_factory=list)
    selected_supplier_id: Optional[str] = None
    commitment: Optional[Commitment] = None
    escalation: Optional[Escalation] = None
    pcp_attempt_count: int = 0
    scenario_name: Optional[str] = None
    scenario_clock: Optional[date] = None
    events: list[Event] = Field(default_factory=list)
    patient_messages: list[str] = Field(default_factory=list)
    awaiting_assignment_consent: bool = False
    assignment_consent: Optional[bool] = None
    supplier_contacts: int = 0
    pcp_contacts: int = 0
    retries: int = 0
