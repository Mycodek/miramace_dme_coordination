"""Read-only FastAPI boundary over patients, suppliers, and cases."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.entities.enums import CaseStatus, EventType
from app.entities.models import Case
from app.repositories.rosters import load_patients, load_suppliers
from app.repositories.sqlite import CaseRepository

app = FastAPI(title="Mira Mace DME Coordinator", version="0.1.0")

_repo: CaseRepository | None = None

_TERMINAL = {CaseStatus.COMPLETE, CaseStatus.ESCALATED}


class CaseMeta(BaseModel):
    case_id: str
    patient_id: str
    status: str
    equipment: str
    created_at: Optional[str] = None
    scenario_name: Optional[str] = None
    selected_supplier_id: Optional[str] = None
    pcp_id: str
    pcp_name: str
    supplier_contacts: int
    pcp_contacts: int
    retries: int
    escalated: bool


class CaseGlance(BaseModel):
    """Quick progress view — not a full case dump."""

    case_id: str
    status: str
    progress: str
    scenario_name: Optional[str] = None
    patient: dict[str, Any]
    doctor: dict[str, Any]
    requirement: dict[str, Any]
    order: Optional[dict[str, Any]] = None
    selected_supplier_id: Optional[str] = None
    commitment: Optional[dict[str, Any]] = None
    escalation: Optional[dict[str, Any]] = None
    contacts: dict[str, int]
    patient_messages: list[str] = Field(default_factory=list)


def get_repo() -> CaseRepository:
    global _repo
    if _repo is None:
        _repo = CaseRepository(db_path=get_settings().cases_db)
    return _repo


def _require_case(case_id: str) -> Case:
    case = get_repo().get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail=f"case not found: {case_id}")
    return case


def _case_created_at_dt(case: Case) -> Optional[datetime]:
    for event in case.events:
        if event.type == EventType.CASE_CREATED:
            return event.timestamp
    if case.created_at:
        return case.created_at
    if case.events:
        return case.events[0].timestamp
    return None


def _case_created_at(case: Case) -> Optional[str]:
    created = _case_created_at_dt(case)
    return created.isoformat() if created else None


def _case_meta(case: Case) -> CaseMeta:
    return CaseMeta(
        case_id=case.case_id,
        patient_id=case.patient.patient_id,
        status=case.status.value,
        equipment=case.equipment,
        created_at=_case_created_at(case),
        scenario_name=case.scenario_name,
        selected_supplier_id=case.selected_supplier_id,
        pcp_id=case.pcp.pcp_id,
        pcp_name=case.pcp.name,
        supplier_contacts=case.supplier_contacts,
        pcp_contacts=case.pcp_contacts,
        retries=case.retries,
        escalated=case.escalation is not None,
    )


def _progress_label(status: CaseStatus) -> str:
    if status == CaseStatus.CREATED:
        return "pending"
    if status in _TERMINAL:
        return "completed" if status == CaseStatus.COMPLETE else "escalated"
    return "in_progress"


def _case_glance(case: Case) -> CaseGlance:
    order_summary = None
    if case.order:
        order_summary = {
            "signed": case.order.signed,
            "hcpcs_code": case.order.hcpcs_code,
            "item_description": case.order.item_description,
            "is_valid": case.order.is_valid,
            "validated": case.order_validated,
        }

    commitment = None
    if case.commitment:
        commitment = {
            "supplier_id": case.commitment.supplier_id,
            "commitment_date": case.commitment.commitment_date.isoformat(),
            "confirmed": bool(case.commitment.confirmed_at),
            "breached": case.commitment.breached,
        }

    escalation = None
    if case.escalation:
        escalation = {
            "reason": case.escalation.reason.value,
            "attempts": case.escalation.attempts,
            "recommended_action": case.escalation.recommended_action,
        }

    return CaseGlance(
        case_id=case.case_id,
        status=case.status.value,
        progress=_progress_label(case.status),
        scenario_name=case.scenario_name,
        patient=case.patient.model_dump(mode="json"),
        doctor=case.pcp.model_dump(mode="json"),
        requirement={"equipment": case.equipment},
        order=order_summary,
        selected_supplier_id=case.selected_supplier_id,
        commitment=commitment,
        escalation=escalation,
        contacts={
            "pcp": case.pcp_contacts,
            "supplier": case.supplier_contacts,
            "retries": case.retries,
        },
        patient_messages=list(case.patient_messages),
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/patients/all")
def patients_all() -> list[dict[str, Any]]:
    settings = get_settings()
    records = load_patients(settings.patients_json)
    return [
        {
            "patient_id": r.patient.patient_id,
            "name": r.patient.name,
            "city": r.patient.city,
            "equipment": r.equipment,
            "pcp": r.pcp.model_dump(mode="json"),
        }
        for r in records.values()
    ]


@app.get("/suppliers/all")
def suppliers_all() -> list[dict[str, Any]]:
    settings = get_settings()
    return [s.model_dump(mode="json") for s in load_suppliers(settings.suppliers_csv)]


@app.get("/cases/all")
def cases_all() -> list[CaseMeta]:
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    cases = sorted(
        get_repo().list_cases(),
        key=lambda c: _case_created_at_dt(c) or epoch,
        reverse=True,
    )
    return [_case_meta(c) for c in cases]


@app.get("/cases/details/{case_id}")
def case_details(case_id: str) -> dict[str, Any]:
    """Full case payload including events and candidate facts."""
    return _require_case(case_id).model_dump(mode="json")


@app.get("/cases/{case_id}")
def case_glance(case_id: str) -> CaseGlance:
    """Quick glance: parties, requirement, progress, final status."""
    return _case_glance(_require_case(case_id))
