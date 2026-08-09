"""CaseOrchestrator — composes workflows; does not interpret transcripts."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from app.config import Settings, get_settings
from app.entities.enums import CaseStatus, EscalationReason, EventType, SupplierCandidateStatus
from app.entities.models import Case, Commitment, Event, PatientRecord, SupplierCandidate, utc_now
from app.llm.factory import LLMClient
from app.llm.providers import FakeLLMClient
from app.mock_scenario import ScenarioWorld
from app.orchestration.state_machine import transition
from app.repositories.document_store import DocumentStore
from app.repositories.sqlite import CaseRepository
from app.repositories.rosters import load_suppliers
from app.policies import POLICY_VERSION, assess_patient_eligibility
from app.helper import build_escalation, can_match, is_overdue
from app.workflows.patient import PatientWorkflow
from app.workflows.pcp import PCPWorkflow
from app.workflows.supplier import SupplierWorkflow


class CaseOrchestrator:
    def __init__(
        self,
        repo: CaseRepository,
        docs: DocumentStore,
        llm: LLMClient,
        world: ScenarioWorld,
        settings: Settings | None = None,
        suppliers_csv: Path | None = None,
    ) -> None:
        self.repo = repo
        self.docs = docs
        self.llm = llm
        self.world = world
        self.settings = settings or get_settings()
        self.suppliers_csv = suppliers_csv or self.settings.suppliers_csv
        self.pcp = PCPWorkflow(repo, docs, llm, world, self.settings)
        self.supplier = SupplierWorkflow(repo, docs, llm, world, self.settings)
        self.patient = PatientWorkflow(repo, docs)
        self._clock_advanced = False

    def create_case(self, record: PatientRecord, scenario_name: str | None = None) -> Case:
        self.world.bind_patient(record)
        if isinstance(self.llm, FakeLLMClient):
            self.llm.bind_patient(record)

        start = self.world.clock_start()
        suppliers = load_suppliers(self.suppliers_csv, prefer_city=record.patient.city)
        elig_ok, elig_errors = assess_patient_eligibility(record.eligibility)
        case = Case(
            status=CaseStatus.CREATED,
            patient=record.patient,
            pcp=record.pcp,
            equipment=record.equipment,
            eligibility=record.eligibility,
            scenario_name=scenario_name,
            scenario_clock=date.fromisoformat(start) if start else date.today(),
            candidates=[
                SupplierCandidate(
                    supplier_id=s.supplier_id,
                    supplier_name=s.supplier_name,
                    phone=s.phone,
                    address=s.address,
                )
                for s in suppliers
            ],
        )
        case.status = transition(case.status, CaseStatus.IN_PROGRESS)
        created = Event(
            type=EventType.CASE_CREATED,
            actor="orchestrator",
            timestamp=case.created_at,
            payload={
                "scenario": scenario_name,
                "patient_id": record.patient.patient_id,
                "suppliers_csv": str(self.suppliers_csv),
                "policy_version": POLICY_VERSION,
                "eligibility_ok": elig_ok,
                "eligibility_errors": elig_errors,
            },
        )
        case.events.append(created)
        self.repo.save_case(case)
        self.patient.notify(
            case,
            "We're working with your doctor's office and checking Medicare-enrolled "
            f"suppliers near {record.patient.city}.",
        )
        if not elig_ok:
            return self._escalate(
                case,
                EscalationReason.PATIENT_NOT_ELIGIBLE,
                attempts=0,
                recommended_action=(
                    "Review K0001 coverage criteria with clinical team: "
                    + "; ".join(elig_errors)
                ),
            )
        return case

    def tick(self, case_id: str) -> Case:
        case = self.repo.get_case(case_id)
        if case is None:
            raise KeyError(case_id)
        if case.status in (CaseStatus.COMPLETE, CaseStatus.ESCALATED):
            return case

        if case.commitment and not case.commitment.confirmed_at and is_overdue(case):
            return self._handle_stall(case)

        if not case.order_validated:
            case = self.pcp.advance(case)
            case = self.repo.get_case(case_id) or case
            if case.status == CaseStatus.ESCALATED and not case.order_validated:
                order_failed = any(
                    e.type == EventType.ORDER_VALIDATION_FAILED for e in case.events
                )
                reason = (
                    EscalationReason.ORDER_INVALID
                    if order_failed
                    else EscalationReason.PCP_UNRESPONSIVE
                )
                return self._escalate(
                    case,
                    reason,
                    attempts=case.pcp_attempt_count,
                    recommended_action=f"Contact {case.pcp.practice}",
                )

        case = self.repo.get_case(case_id) or case
        has_qualified = any(
            c.status == SupplierCandidateStatus.QUALIFIED for c in case.candidates
        )
        if (
            not has_qualified
            and case.status not in (CaseStatus.ESCALATED, CaseStatus.COMPLETE)
            and not (case.commitment and not case.commitment.breached)
        ):
            case = self.supplier.advance(case)
            case = self.repo.get_case(case_id) or case
            if case.status == CaseStatus.ESCALATED and not has_qualified:
                return self._escalate_supplier_search_failed(case)

        case = self.repo.get_case(case_id) or case
        matched = can_match(case)
        if matched and case.status not in (
            CaseStatus.DELIVERY_COMMITTED,
            CaseStatus.DELIVERY_CONFIRMED,
            CaseStatus.COMPLETE,
            CaseStatus.ESCALATED,
        ):
            return self._match(case, matched)

        if (
            case.status == CaseStatus.DELIVERY_COMMITTED
            and case.commitment
            and not case.commitment.confirmed_at
            and self.world.auto_confirm_delivery()
        ):
            return self._complete(case)

        self.repo.save_case(case)
        return case

    def run_until_terminal(self, case_id: str, max_ticks: int = 40) -> Case:
        case = self.repo.get_case(case_id)
        if case is None:
            raise KeyError(case_id)
        for _ in range(max_ticks):
            case = self.tick(case_id)
            if case.status in (CaseStatus.COMPLETE, CaseStatus.ESCALATED):
                break
            adv = self.world.advance_after_commit_to()
            if (
                not self._clock_advanced
                and case.commitment
                and not case.commitment.confirmed_at
                and adv
                and case.scenario_clock
                and case.scenario_clock <= case.commitment.commitment_date
            ):
                case.scenario_clock = date.fromisoformat(adv)
                self._clock_advanced = True
                self.repo.save_case(case)
        return case

    def resume(self, case_id: str) -> Case:
        return self.tick(case_id)

    def _match(self, case: Case, candidate: SupplierCandidate) -> Case:
        case.status = transition(case.status, CaseStatus.READY_TO_MATCH)
        case.selected_supplier_id = candidate.supplier_id
        eta = 2
        if candidate.facts and candidate.facts.delivery_eta_days is not None:
            eta = candidate.facts.delivery_eta_days
        day = (case.scenario_clock or date.today()) + timedelta(days=eta)
        case.commitment = Commitment(supplier_id=candidate.supplier_id, commitment_date=day)
        case.status = transition(case.status, CaseStatus.DELIVERY_COMMITTED)
        case.events.append(
            Event(
                type=EventType.DELIVERY_COMMITTED,
                actor="orchestrator",
                payload={
                    "supplier": candidate.supplier_name,
                    "commitment_date": day.isoformat(),
                },
            )
        )
        print(
            f"[{utc_now().strftime('%H:%M:%S')}] case={case.case_id} "
            f"workflow=orchestrator action=MATCH supplier={candidate.supplier_name} "
            f"commitment={day}"
        )
        self.patient.notify(
            case,
            f"Your DME supplier has been identified ({candidate.supplier_name}). "
            f"Delivery is currently scheduled for {day.isoformat()}.",
        )
        if self.world.stall_commitment() and not self._clock_advanced:
            self.repo.save_case(case)
            return case
        if self.world.stall_commitment() and self._clock_advanced:
            return self._complete(case)
        if self.world.auto_confirm_delivery():
            return self._complete(case)
        self.repo.save_case(case)
        return case

    def _handle_stall(self, case: Case) -> Case:
        stalled_supplier_id = case.selected_supplier_id
        stalled_date = (
            case.commitment.commitment_date.isoformat() if case.commitment else None
        )
        case.events.append(
            Event(
                type=EventType.COMMITMENT_STALLED,
                actor="orchestrator",
                payload={
                    "commitment_date": stalled_date,
                    "supplier_id": stalled_supplier_id,
                },
            )
        )
        print(
            f"[{utc_now().strftime('%H:%M:%S')}] case={case.case_id} "
            f"workflow=orchestrator action=COMMITMENT_STALLED "
            f"supplier={stalled_supplier_id}"
        )
        case = self.supplier.follow_up_commitment(case)
        case = self.repo.get_case(case.case_id) or case
        if case.commitment and case.commitment.confirmed_at:
            return self._complete(case)

        failed = next(
            (c for c in case.candidates if c.supplier_id == stalled_supplier_id),
            None,
        )
        supplier_name = failed.supplier_name if failed else "Your DME supplier"
        schedule = f" for {stalled_date}" if stalled_date else ""
        self.patient.notify(
            case,
            f"Unfortunately, {supplier_name} could not fulfill the delivery"
            f"{schedule} (supplier declined / did not confirm). "
            "We're arranging a replacement Medicare-enrolled supplier now.",
        )

        case.selected_supplier_id = None
        case.commitment = None
        case.status = transition(case.status, CaseStatus.SUPPLIER_SEARCH)
        self.repo.save_case(case)
        case = self.supplier.advance(case)
        case = self.repo.get_case(case.case_id) or case
        matched = can_match(case)
        if matched:
            return self._match(case, matched)

        recoverable = [
            c
            for c in case.candidates
            if c.status
            in (
                SupplierCandidateStatus.DISCOVERED,
                SupplierCandidateStatus.NO_ANSWER,
                SupplierCandidateStatus.K0001_OUT_OF_STOCK,
                SupplierCandidateStatus.QUALIFIED,
            )
        ]
        if not recoverable:
            return self._escalate(
                case,
                EscalationReason.SUPPLIER_COMMITMENT_BROKEN,
                attempts=case.supplier_contacts,
                recommended_action=(
                    f"Contact {case.patient.name} and assign a replacement supplier"
                ),
            )
        self.repo.save_case(case)
        return case

    def _assignment_refusers(self, case: Case) -> list[SupplierCandidate]:
        return [
            c
            for c in case.candidates
            if c.status == SupplierCandidateStatus.DOES_NOT_ACCEPT_ASSIGNMENT
            or (c.facts is not None and c.facts.accepts_assignment is False)
        ]

    def _ask_assignment_consent(self, case: Case) -> Case:
        refusers = self._assignment_refusers(case)
        if not refusers or case.awaiting_assignment_consent or case.assignment_consent is not None:
            return case
        sample = ", ".join(c.supplier_name for c in refusers[:3])
        more = f" (and {len(refusers) - 3} others)" if len(refusers) > 3 else ""
        self.patient.ask_assignment_consent(
            case,
            f"Suppliers such as {sample}{more} can provide your equipment but do not "
            f"accept Medicare assignment — you may owe more than the standard 20% "
            f"coinsurance. Reply yes to proceed with a non-assignment supplier, or no "
            f"to decline and escalate to our care team.",
        )
        return self.repo.get_case(case.case_id) or case

    def _book_with_assignment_waiver(self, case: Case) -> Case | None:
        """Promote first viable non-assignment supplier and match after patient yes."""
        for candidate in case.candidates:
            if candidate.status != SupplierCandidateStatus.DOES_NOT_ACCEPT_ASSIGNMENT:
                continue
            facts = candidate.facts
            if not facts:
                continue
            if not (
                facts.accepting_new_patients is True
                and facts.medicare_part_b is True
                and facts.k0001_available is True
                and facts.delivery_possible is True
            ):
                continue
            candidate.status = SupplierCandidateStatus.QUALIFIED
            case.status = CaseStatus.SUPPLIER_QUALIFIED
            case.escalation = None
            self.repo.save_case(case)
            return self._match(case, candidate)
        return None

    def _resolve_assignment_consent(self, case: Case) -> Case:
        """Ask patient yes/no; on yes book non-assignment supplier, on no escalate."""
        # Supplier lane may have soft-set ESCALATED with no escalation payload yet.
        if case.status == CaseStatus.ESCALATED and case.escalation is None:
            case.status = CaseStatus.SUPPLIER_SEARCH
            self.repo.save_case(case)

        case = self._ask_assignment_consent(case)

        if case.assignment_consent is None:
            raw = self.world.patient_assignment_consent()
            if raw is None:
                return self._escalate(
                    case,
                    EscalationReason.ASSIGNMENT_CONSENT_REQUIRED,
                    attempts=case.supplier_contacts,
                    recommended_action=(
                        "Await patient yes/no on balance-billing risk, or source a "
                        f"DME supplier near {case.patient.city} that accepts assignment"
                    ),
                )
            accepted = raw in {"yes", "y", "true", "accept", "1"}
            self.patient.apply_assignment_consent(case, accepted)
            case = self.repo.get_case(case.case_id) or case

        if case.assignment_consent:
            booked = self._book_with_assignment_waiver(case)
            if booked:
                return booked
            return self._escalate(
                case,
                EscalationReason.NO_SUPPLIER_AVAILABLE,
                attempts=case.supplier_contacts,
                recommended_action=(
                    f"Patient consented but no bookable non-assignment supplier near "
                    f"{case.patient.city}"
                ),
            )

        return self._escalate(
            case,
            EscalationReason.ASSIGNMENT_CONSENT_DECLINED,
            attempts=case.supplier_contacts,
            recommended_action=(
                "Patient declined non-assignment suppliers; source a Medicare "
                f"assignment-accepting DME supplier near {case.patient.city}"
            ),
        )

    def _escalate_supplier_search_failed(self, case: Case) -> Case:
        refusers = self._assignment_refusers(case)
        if refusers and len(refusers) >= max(1, len(case.candidates) // 2):
            return self._resolve_assignment_consent(case)
        return self._escalate(
            case,
            EscalationReason.NO_SUPPLIER_AVAILABLE,
            attempts=case.supplier_contacts,
            recommended_action=(
                f"Manually source a Medicare DME supplier near {case.patient.city}"
            ),
        )

    def _complete(self, case: Case) -> Case:
        if case.commitment and not case.commitment.confirmed_at:
            case.commitment.confirmed_at = utc_now()
        for c in case.candidates:
            if c.supplier_id == case.selected_supplier_id:
                c.status = SupplierCandidateStatus.DELIVERY_CONFIRMED
        case.status = transition(case.status, CaseStatus.DELIVERY_CONFIRMED)
        case.events.append(
            Event(type=EventType.DELIVERY_CONFIRMED, actor="orchestrator", payload={})
        )
        case.status = transition(case.status, CaseStatus.COMPLETE)
        case.events.append(
            Event(type=EventType.CASE_COMPLETED, actor="orchestrator", payload={})
        )
        print(
            f"[{utc_now().strftime('%H:%M:%S')}] case={case.case_id} "
            f"workflow=orchestrator action=COMPLETE result=SUCCESS"
        )
        self.repo.save_case(case)
        return case

    def _escalate(
        self,
        case: Case,
        reason: EscalationReason,
        *,
        attempts: int,
        recommended_action: str,
    ) -> Case:
        case.status = CaseStatus.ESCALATED
        case.escalation = build_escalation(
            case, reason, attempts=attempts, recommended_action=recommended_action
        )
        case.events.append(
            Event(
                type=EventType.HUMAN_ESCALATION,
                actor="orchestrator",
                payload=case.escalation.model_dump(mode="json"),
            )
        )
        print(
            f"[{utc_now().strftime('%H:%M:%S')}] case={case.case_id} "
            f"workflow=orchestrator action=ESCALATE reason={reason.value} "
            f"attempts={attempts}"
        )
        self.repo.save_case(case)
        return case
