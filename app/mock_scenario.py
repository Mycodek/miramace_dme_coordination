"""Scenario-driven mock external world (PCP / supplier contacts)."""

from __future__ import annotations

import json
from pathlib import Path

from app.entities.models import PatientRecord


class ScenarioWorld:
    def __init__(self, scenario: dict, record: PatientRecord | None = None) -> None:
        self.scenario = scenario
        self.record = record
        self.pcp_attempt_index = 0

    @classmethod
    def load(cls, path: Path, record: PatientRecord | None = None) -> ScenarioWorld:
        return cls(json.loads(path.read_text(encoding="utf-8")), record=record)

    def bind_patient(self, record: PatientRecord) -> None:
        self.record = record

    def _ctx(self) -> dict[str, str]:
        if not self.record:
            return {}
        return {
            "patient_name": self.record.patient.name,
            "patient_city": self.record.patient.city,
            "equipment": self.record.equipment,
            "pcp_name": self.record.pcp.name,
            "practice": self.record.pcp.practice,
            "hcpcs": "K0001",
        }

    def _render(self, template: str) -> str:
        if not template:
            return template
        try:
            return template.format(**self._ctx())
        except KeyError:
            return template

    def pcp_contact(self) -> tuple[str, str]:
        attempts = self.scenario.get("pcp", {}).get("attempts", ["submitted_valid"])
        outcome = attempts[min(self.pcp_attempt_index, len(attempts) - 1)]
        self.pcp_attempt_index += 1
        return outcome, self._render(
            self.scenario.get("transcripts", {}).get(f"pcp.{outcome}", "")
        )

    def supplier_contact(self, supplier_name: str) -> tuple[str, str]:
        outcome = self.scenario.get("suppliers", {}).get(
            supplier_name, self.scenario.get("default_supplier_outcome", "no_answer")
        )
        return outcome, self._render(
            self.scenario.get("transcripts", {}).get(f"supplier.{outcome}", "")
        )

    def supplier_follow_up(self, supplier_name: str) -> tuple[str, str]:
        outcome = self.scenario.get("follow_up", {}).get(
            supplier_name, self.scenario.get("default_follow_up_outcome", "silent")
        )
        return outcome, self._render(
            self.scenario.get("transcripts", {}).get(f"supplier.{outcome}", "")
        )

    def auto_confirm_delivery(self) -> bool:
        return bool(self.scenario.get("auto_confirm_delivery", False))

    def stall_commitment(self) -> bool:
        return bool(self.scenario.get("stall_commitment", False))

    def patient_assignment_consent(self) -> str | None:
        """Mock patient yes/no for non-assignment suppliers. Values: yes|no|None."""
        raw = (self.scenario.get("patient") or {}).get("assignment_consent")
        if raw is None:
            return None
        return str(raw).strip().lower()

    def clock_start(self) -> str | None:
        return (self.scenario.get("clock") or {}).get("start")

    def advance_after_commit_to(self) -> str | None:
        return (self.scenario.get("clock") or {}).get("advance_after_commit_to")
