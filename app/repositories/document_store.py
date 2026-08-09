"""In-memory document store for transcripts / extracted facts."""

from __future__ import annotations


class DocumentStore:
    def __init__(self) -> None:
        self._docs: dict[str, dict] = {}

    def put_interaction(
        self,
        *,
        case_id: str,
        party: str,
        transcript: str,
        extracted_facts: dict,
        supplier_id: str | None = None,
    ) -> str:
        key = f"{case_id}:{party}:{len(self._docs)}"
        self._docs[key] = {
            "case_id": case_id,
            "party": party,
            "supplier_id": supplier_id,
            "transcript": transcript,
            "extracted_facts": extracted_facts,
        }
        return key

    def list_for_case(self, case_id: str) -> list[dict]:
        return [d for d in self._docs.values() if d["case_id"] == case_id]
