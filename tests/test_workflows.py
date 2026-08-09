from app.config import get_settings
from app.entities.enums import CaseStatus, EscalationReason, EventType, SupplierCandidateStatus
from app.llm.providers import FakeLLMClient
from app.mock_scenario import ScenarioWorld
from app.orchestration.coordinator import CaseOrchestrator
from app.repositories.document_store import DocumentStore
from app.repositories.rosters import get_patient
from app.repositories.sqlite import CaseRepository
from app.helper import QualifyResult, qualify_supplier, validate_order
from app.entities.models import Order, PatientEligibility, SupplierFacts
from app.policies import assess_patient_eligibility


def _run(scenario: str, patient_id: str = "PAT-ELEANOR"):
    settings = get_settings()
    record = get_patient(settings.patients_json, patient_id)
    world = ScenarioWorld.load(settings.scenarios_dir / f"{scenario}.json", record)
    orch = CaseOrchestrator(
        CaseRepository(), DocumentStore(), FakeLLMClient(record), world, settings
    )
    return orch, record


def test_validate_and_qualify():
    order = Order(
        patient_name="A",
        order_date="2026-08-09",
        item_description="standard manual wheelchair",
        practitioner_name="Dr",
        signed=True,
        hcpcs_code="K0001",
        face_to_face=True,
        home_assessment=True,
    )
    ok, _ = validate_order(order, "standard manual wheelchair")
    assert ok
    assert (
        qualify_supplier(
            SupplierFacts(
                accepting_new_patients=True,
                medicare_part_b=True,
                k0001_available=True,
                delivery_possible=True,
                accepts_assignment=True,
            )
        )
        == QualifyResult.QUALIFIED
    )
    assert (
        qualify_supplier(
            SupplierFacts(
                accepting_new_patients=True,
                medicare_part_b=True,
                k0001_available=True,
                delivery_possible=True,
                accepts_assignment=False,
            )
        )
        == QualifyResult.REJECTED
    )
    ok_elig, errs = assess_patient_eligibility(
        PatientEligibility(outdoor_use_only=True)
    )
    assert not ok_elig and any("outdoor" in e for e in errs)


def test_happy_path():
    orch, record = _run("happy_path")
    case = orch.create_case(record, "happy_path")
    case = orch.run_until_terminal(case.case_id)
    assert case.status == CaseStatus.COMPLETE
    assert case.order_validated
    assert EventType.CASE_COMPLETED in [e.type for e in case.events]


def test_alternate_patient():
    orch, record = _run("happy_path", "PAT-JAMES")
    case = orch.create_case(record, "happy_path")
    case = orch.run_until_terminal(case.case_id)
    assert case.status == CaseStatus.COMPLETE
    assert case.patient.patient_id == "PAT-JAMES"


def test_pcp_timeout():
    orch, record = _run("pcp_timeout")
    case = orch.create_case(record, "pcp_timeout")
    case = orch.run_until_terminal(case.case_id)
    assert case.status == CaseStatus.ESCALATED
    assert case.escalation and case.escalation.reason == EscalationReason.PCP_UNRESPONSIVE


def test_supplier_failure():
    orch, record = _run("supplier_failure")
    case = orch.create_case(record, "supplier_failure")
    case = orch.run_until_terminal(case.case_id)
    assert case.status == CaseStatus.COMPLETE
    assert any(e.type == EventType.COMMITMENT_STALLED for e in case.events)
    assert any(c.status == SupplierCandidateStatus.FAILED for c in case.candidates)
    assert len(case.patient_messages) >= 3
    assert any("could not fulfill" in m for m in case.patient_messages)
    fail_idx = next(i for i, m in enumerate(case.patient_messages) if "could not fulfill" in m)
    book_idxs = [
        i
        for i, m in enumerate(case.patient_messages)
        if "supplier has been identified" in m
    ]
    assert len(book_idxs) >= 2
    assert book_idxs[0] < fail_idx < book_idxs[1]


def test_happy_path_direct():
    orch, record = _run("happy_path_direct")
    case = orch.create_case(record, "happy_path_direct")
    case = orch.run_until_terminal(case.case_id)
    assert case.status == CaseStatus.COMPLETE


def test_pcp_incomplete_order():
    orch, record = _run("pcp_incomplete_order")
    case = orch.create_case(record, "pcp_incomplete_order")
    case = orch.run_until_terminal(case.case_id)
    assert case.status == CaseStatus.ESCALATED
    assert case.escalation and case.escalation.reason == EscalationReason.ORDER_INVALID


def test_supplier_no_assignment_patient_yes():
    orch, record = _run("supplier_no_assignment")
    case = orch.create_case(record, "supplier_no_assignment")
    case = orch.run_until_terminal(case.case_id)
    assert case.status == CaseStatus.COMPLETE
    assert case.assignment_consent is True
    assert any(e.type == EventType.PATIENT_RESPONSE_RECEIVED for e in case.events)
    assert any("Reply yes" in m or "do not accept Medicare assignment" in m for m in case.patient_messages)
    assert case.selected_supplier_id is not None


def test_supplier_no_assignment_patient_no():
    orch, record = _run("supplier_no_assignment_declined")
    case = orch.create_case(record, "supplier_no_assignment_declined")
    case = orch.run_until_terminal(case.case_id)
    assert case.status == CaseStatus.ESCALATED
    assert case.assignment_consent is False
    assert (
        case.escalation
        and case.escalation.reason == EscalationReason.ASSIGNMENT_CONSENT_DECLINED
    )
    assert any(e.type == EventType.PATIENT_RESPONSE_RECEIVED for e in case.events)


def test_policy_weight_ineligible():
    orch, record = _run("policy_weight_ineligible", "PAT-MARCUS")
    case = orch.create_case(record, "policy_weight_ineligible")
    case = orch.run_until_terminal(case.case_id)
    assert case.status == CaseStatus.ESCALATED
    assert case.escalation and case.escalation.reason == EscalationReason.PATIENT_NOT_ELIGIBLE
    assert any(
        e.payload.get("eligibility_ok") is False
        for e in case.events
        if e.type == EventType.CASE_CREATED
    )
