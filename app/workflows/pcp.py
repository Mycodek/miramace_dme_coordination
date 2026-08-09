from __future__ import annotations

from app.config import Settings
from app.entities.enums import CaseStatus, EventType
from app.entities.models import Case, Event, Order, utc_now
from app.llm.factory import LLMClient
from app.mock_scenario import ScenarioWorld
from app.repositories.document_store import DocumentStore
from app.repositories.sqlite import CaseRepository
from app.helper import validate_order


class PCPWorkflow:
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
        if case.order_validated:
            return case

        case.status = CaseStatus.PCP_ORDER_PENDING
        case.pcp_attempt_count += 1
        case.pcp_contacts += 1
        outcome, transcript = self.world.pcp_contact()
        self._event(
            case,
            EventType.PCP_CONTACT_ATTEMPTED,
            {"attempt": case.pcp_attempt_count, "outcome": outcome},
        )
        print(
            f"[{utc_now().strftime('%H:%M:%S')}] case={case.case_id} "
            f"workflow=pcp action=CONTACT_PCP attempt={case.pcp_attempt_count} "
            f"result={outcome}"
        )

        if outcome == "no_answer" or not transcript.strip():
            self._event(case, EventType.PCP_RESPONSE_RECEIVED, {"no_response": True})
            if case.pcp_attempt_count >= self.settings.pcp_max_attempts:
                case.status = CaseStatus.ESCALATED
            else:
                case.retries += 1
                self._event(
                    case,
                    EventType.RETRY_SCHEDULED,
                    {"attempt": case.pcp_attempt_count},
                )
            self.repo.save_case(case)
            return case

        extraction = self.llm.extract_pcp_response(transcript)
        self.docs.put_interaction(
            case_id=case.case_id,
            party="pcp",
            transcript=transcript,
            extracted_facts=extraction.model_dump(),
        )
        self._event(case, EventType.PCP_RESPONSE_RECEIVED, extraction.model_dump())

        order = Order(
            patient_name=extraction.patient_name,
            order_date=extraction.order_date,
            item_description=extraction.item_description,
            practitioner_name=extraction.practitioner_name,
            signed=bool(extraction.signed),
            hcpcs_code=extraction.hcpcs_code,
            face_to_face=bool(extraction.face_to_face),
            home_assessment=bool(extraction.home_assessment),
        )
        ok, errors = validate_order(order, case.equipment)
        order.is_valid = ok
        order.validation_errors = errors
        case.order = order

        if ok:
            case.order_validated = True
            case.status = CaseStatus.ORDER_VALIDATED
            self._event(case, EventType.ORDER_VALIDATED, {})
            print(
                f"[{utc_now().strftime('%H:%M:%S')}] case={case.case_id} "
                f"workflow=pcp action=VALIDATE_ORDER result=VALID"
            )
        else:
            self._event(case, EventType.ORDER_VALIDATION_FAILED, {"errors": errors})
            print(
                f"[{utc_now().strftime('%H:%M:%S')}] case={case.case_id} "
                f"workflow=pcp action=VALIDATE_ORDER result=INVALID"
            )
            if case.pcp_attempt_count >= self.settings.pcp_max_attempts:
                case.status = CaseStatus.ESCALATED
            else:
                case.retries += 1

        self.repo.save_case(case)
        return case

    def _event(self, case: Case, etype: EventType, payload: dict) -> None:
        case.events.append(Event(type=etype, actor="pcp_workflow", payload=payload))
