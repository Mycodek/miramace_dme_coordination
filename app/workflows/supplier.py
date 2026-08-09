from __future__ import annotations

from app.config import Settings
from app.entities.enums import CaseStatus, EventType, SupplierCandidateStatus
from app.entities.models import Case, Event, SupplierCandidate, utc_now
from app.llm.factory import LLMClient
from app.mock_scenario import ScenarioWorld
from app.repositories.document_store import DocumentStore
from app.repositories.sqlite import CaseRepository
from app.helper import QualifyResult, qualify_supplier


class SupplierWorkflow:
    def __init__(
        self,
        repo: CaseRepository,
        docs: DocumentStore,
        llm: LLMClient,
        world: ScenarioWorld,
        settings: Settings,
    ) -> None:
        self.repo = repo
        self.docs = docs
        self.llm = llm
        self.world = world
        self.settings = settings

    def advance(self, case: Case) -> Case:
        if any(c.status == SupplierCandidateStatus.QUALIFIED for c in case.candidates):
            if case.status not in (
                CaseStatus.READY_TO_MATCH,
                CaseStatus.DELIVERY_COMMITTED,
                CaseStatus.DELIVERY_CONFIRMED,
                CaseStatus.COMPLETE,
                CaseStatus.ESCALATED,
            ):
                case.status = CaseStatus.SUPPLIER_QUALIFIED
                self.repo.save_case(case)
            return case

        case.status = CaseStatus.SUPPLIER_SEARCH
        actionable = [
            c
            for c in case.candidates
            if c.status
            in (
                SupplierCandidateStatus.DISCOVERED,
                SupplierCandidateStatus.NO_ANSWER,
                SupplierCandidateStatus.K0001_OUT_OF_STOCK,
            )
            and c.attempt_count < self.settings.supplier_max_attempts
        ]
        if not actionable:
            if not any(c.status == SupplierCandidateStatus.QUALIFIED for c in case.candidates):
                case.status = CaseStatus.ESCALATED
            self.repo.save_case(case)
            return case

        self._contact(case, actionable[0])
        return case

    def follow_up_commitment(self, case: Case) -> Case:
        selected = next(
            (c for c in case.candidates if c.supplier_id == case.selected_supplier_id),
            None,
        )
        if not selected or not case.commitment:
            return case

        outcome, transcript = self.world.supplier_follow_up(selected.supplier_name)
        self._event(
            case,
            EventType.SUPPLIER_CONTACT_ATTEMPTED,
            {"follow_up": True, "outcome": outcome, "supplier": selected.supplier_name},
        )
        print(
            f"[{utc_now().strftime('%H:%M:%S')}] case={case.case_id} "
            f"workflow=supplier action=FOLLOW_UP supplier={selected.supplier_name} "
            f"result={outcome}"
        )

        if outcome == "confirmed" and transcript.strip():
            case.commitment.confirmed_at = utc_now()
            selected.status = SupplierCandidateStatus.DELIVERY_CONFIRMED
            self.repo.save_case(case)
            return case

        case.commitment.breached = True
        selected.status = SupplierCandidateStatus.FAILED
        self._event(
            case,
            EventType.DELIVERY_FAILED,
            {"supplier": selected.supplier_name, "reason": "commitment_breach"},
        )
        self.repo.save_case(case)
        return case

    def _contact(self, case: Case, candidate: SupplierCandidate) -> None:
        candidate.attempt_count += 1
        case.supplier_contacts += 1
        outcome, transcript = self.world.supplier_contact(candidate.supplier_name)
        self._event(
            case,
            EventType.SUPPLIER_CONTACT_ATTEMPTED,
            {
                "supplier": candidate.supplier_name,
                "attempt": candidate.attempt_count,
                "outcome": outcome,
            },
        )
        print(
            f"[{utc_now().strftime('%H:%M:%S')}] case={case.case_id} "
            f"workflow=supplier action=CONTACT_SUPPLIER "
            f"supplier={candidate.supplier_name} attempt={candidate.attempt_count} "
            f"result={outcome}"
        )

        facts = self.llm.extract_supplier_response(transcript)
        if outcome == "no_answer" or not transcript.strip():
            facts.no_answer = True
        self.docs.put_interaction(
            case_id=case.case_id,
            party="supplier",
            transcript=transcript,
            extracted_facts=facts.model_dump(),
            supplier_id=candidate.supplier_id,
        )
        candidate.facts = facts
        self._event(
            case,
            EventType.SUPPLIER_RESPONSE_RECEIVED,
            {"supplier": candidate.supplier_name, "facts": facts.model_dump()},
        )

        result = qualify_supplier(facts)
        if result == QualifyResult.QUALIFIED:
            candidate.status = SupplierCandidateStatus.QUALIFIED
            case.status = CaseStatus.SUPPLIER_QUALIFIED
            self._event(
                case,
                EventType.SUPPLIER_QUALIFIED,
                {"supplier": candidate.supplier_name},
            )
            print(
                f"[{utc_now().strftime('%H:%M:%S')}] case={case.case_id} "
                f"workflow=supplier action=QUALIFY result=QUALIFIED "
                f"supplier={candidate.supplier_name}"
            )
        elif result == QualifyResult.RETRYABLE:
            candidate.status = (
                SupplierCandidateStatus.K0001_OUT_OF_STOCK
                if facts.out_of_stock or outcome == "out_of_stock"
                else SupplierCandidateStatus.NO_ANSWER
            )
            if candidate.attempt_count >= self.settings.supplier_max_attempts:
                candidate.status = SupplierCandidateStatus.FAILED
            else:
                case.retries += 1
                self._event(
                    case,
                    EventType.RETRY_SCHEDULED,
                    {"supplier": candidate.supplier_name},
                )
            self._event(
                case,
                EventType.SUPPLIER_REJECTED,
                {"supplier": candidate.supplier_name, "retryable": True},
            )
        else:
            if facts.accepting_new_patients is False or outcome == "not_accepting":
                candidate.status = SupplierCandidateStatus.NOT_ACCEPTING_NEW_PATIENTS
            elif facts.k0001_available is False or outcome == "no_k0001":
                candidate.status = SupplierCandidateStatus.DOES_NOT_PROVIDE_K0001
            elif facts.accepts_assignment is False or outcome == "no_assignment":
                candidate.status = SupplierCandidateStatus.DOES_NOT_ACCEPT_ASSIGNMENT
            else:
                candidate.status = SupplierCandidateStatus.FAILED
            self._event(
                case,
                EventType.SUPPLIER_REJECTED,
                {
                    "supplier": candidate.supplier_name,
                    "reason": candidate.status.value,
                },
            )
            print(
                f"[{utc_now().strftime('%H:%M:%S')}] case={case.case_id} "
                f"workflow=supplier action=QUALIFY result=REJECTED "
                f"supplier={candidate.supplier_name} reason={candidate.status.value}"
            )
        self.repo.save_case(case)

    def _event(self, case: Case, etype: EventType, payload: dict) -> None:
        case.events.append(Event(type=etype, actor="supplier_workflow", payload=payload))
