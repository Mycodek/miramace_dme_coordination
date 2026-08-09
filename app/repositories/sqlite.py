"""SQLite persistence — case JSON blobs (extendable to normalized tables)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.entities.models import Case


class CaseRepository:
    def __init__(
        self,
        connection: sqlite3.Connection | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        if connection is not None:
            self.conn = connection
        elif db_path is None or str(db_path) == ":memory:":
            self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            path = Path(db_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS cases ("
            "case_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        self.conn.commit()

    def save_case(self, case: Case) -> None:
        self.conn.execute(
            "INSERT INTO cases(case_id, payload) VALUES(?, ?) "
            "ON CONFLICT(case_id) DO UPDATE SET payload=excluded.payload",
            (case.case_id, case.model_dump_json()),
        )
        self.conn.commit()

    def get_case(self, case_id: str) -> Case | None:
        row = self.conn.execute(
            "SELECT payload FROM cases WHERE case_id=?", (case_id,)
        ).fetchone()
        return Case.model_validate_json(row[0]) if row else None

    def list_cases(self) -> list[Case]:
        rows = self.conn.execute(
            "SELECT payload FROM cases ORDER BY case_id"
        ).fetchall()
        return [Case.model_validate_json(row[0]) for row in rows]

    def find_cases(
        self,
        *,
        patient_id: str | None = None,
        case_id: str | None = None,
    ) -> list[Case]:
        cases = self.list_cases()
        if case_id:
            cases = [c for c in cases if c.case_id == case_id]
        if patient_id:
            cases = [c for c in cases if c.patient.patient_id == patient_id]
        return cases
