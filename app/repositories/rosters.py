"""Patient + supplier roster loaders (JSON / CSV)."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from app.entities.models import Patient, PatientEligibility, PatientRecord, PCP, Supplier


def load_patients(path: Path) -> dict[str, PatientRecord]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, PatientRecord] = {}
    for pid, item in raw.items():
        pcp = item["pcp"]
        eligibility = PatientEligibility.model_validate(item.get("eligibility") or {})
        out[pid] = PatientRecord(
            patient=Patient(
                patient_id=item["patient_id"], name=item["name"], city=item["city"]
            ),
            pcp=PCP(
                pcp_id=pcp["pcp_id"],
                name=pcp["name"],
                practice=pcp["practice"],
                phone=pcp["phone"],
            ),
            equipment=item["equipment"],
            eligibility=eligibility,
        )
    return out


def get_patient(path: Path, patient_id: str) -> PatientRecord:
    patients = load_patients(path)
    if patient_id not in patients:
        raise KeyError(
            f"unknown patient_id={patient_id}; known: {', '.join(sorted(patients))}"
        )
    return patients[patient_id]


def _stable_supplier_id(name: str, phone: str) -> str:
    digest = hashlib.sha1(f"{name}|{phone}".encode()).hexdigest()[:8]
    return f"SUP-{digest}"


def load_suppliers(csv_path: Path, prefer_city: str | None = None) -> list[Supplier]:
    suppliers: list[Supplier] = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = row["supplier_name"].strip()
            phone = row["phone"].strip()
            suppliers.append(
                Supplier(
                    supplier_id=_stable_supplier_id(name, phone),
                    supplier_name=name,
                    phone=phone,
                    address=row["address"].strip(),
                )
            )
    return rank_suppliers(suppliers, prefer_city=prefer_city)


def rank_suppliers(
    suppliers: list[Supplier], prefer_city: str | None = None
) -> list[Supplier]:
    if not prefer_city:
        return list(suppliers)
    city = prefer_city.lower()
    local = [s for s in suppliers if city in s.address.lower()]
    other = [s for s in suppliers if city not in s.address.lower()]
    return local + other
