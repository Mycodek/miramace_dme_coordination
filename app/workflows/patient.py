from __future__ import annotations

from app.entities.enums import EventType
from app.entities.models import Case, Event, utc_now
from app.repositories.document_store import DocumentStore
from app.repositories.sqlite import CaseRepository


class PatientWorkflow:
    def __init__(self, repo: CaseRepository, docs: DocumentStore | None = None) -> None:
        self.repo = repo
        self.docs = docs

    def notify(self, case: Case, message: str) -> None:
        case.patient_messages.append(message)
        case.events.append(
            Event(
                type=EventType.PATIENT_NOTIFIED,
                actor="patient_workflow",
                payload={"message": message},
            )
        )
        print(
            f"[{case.events[-1].timestamp.strftime('%H:%M:%S')}] case={case.case_id} "
            f"workflow=patient action=NOTIFY result=SENT"
        )
        self.repo.save_case(case)

    def ask_assignment_consent(self, case: Case, message: str) -> None:
        """Ask patient yes/no about proceeding without Medicare assignment (20% only)."""
        self.notify(case, message)
        case.awaiting_assignment_consent = True
        self.repo.save_case(case)

    def apply_assignment_consent(self, case: Case, accepted: bool) -> None:
        """Record patient yes/no and clear awaiting flag."""
        case.assignment_consent = accepted
        case.awaiting_assignment_consent = False
        reply = "yes" if accepted else "no"
        ack = (
            f"Patient replied '{reply}' to proceeding with a supplier that does not "
            f"accept Medicare assignment (possible balance billing above 20% coinsurance)."
        )
        case.patient_messages.append(ack)
        case.events.append(
            Event(
                type=EventType.PATIENT_RESPONSE_RECEIVED,
                actor="patient_workflow",
                timestamp=utc_now(),
                payload={"assignment_consent": accepted, "reply": reply},
            )
        )
        print(
            f"[{case.events[-1].timestamp.strftime('%H:%M:%S')}] case={case.case_id} "
            f"workflow=patient action=ASSIGNMENT_CONSENT result={reply.upper()}"
        )
        self.repo.save_case(case)
