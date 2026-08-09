"""CLI demo for Mira Mace DME coordination scenarios."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import get_settings
from app.entities.enums import CaseStatus
from app.llm.factory import build_llm_client
from app.mock_scenario import ScenarioWorld
from app.orchestration.coordinator import CaseOrchestrator
from app.repositories.document_store import DocumentStore
from app.repositories.rosters import get_patient, load_patients
from app.repositories.sqlite import CaseRepository


def build_orchestrator(
    scenario: str,
    *,
    use_fake_llm: bool = False,
    suppliers_csv: Path | None = None,
) -> tuple[CaseOrchestrator, Path]:
    get_settings.cache_clear()
    settings = get_settings()
    path = settings.scenarios_dir / f"{scenario}.json"
    if not path.exists():
        raise SystemExit(f"Unknown scenario: {scenario} (expected {path})")
    world = ScenarioWorld.load(path)
    csv_path = suppliers_csv or settings.suppliers_csv
    if not csv_path.exists():
        raise SystemExit(f"Suppliers CSV not found: {csv_path}")

    llm, label = build_llm_client(force_fake=use_fake_llm)
    print(f"Using LLM provider={label}\n")

    orch = CaseOrchestrator(
        CaseRepository(db_path=settings.cases_db),
        DocumentStore(),
        llm,
        world,
        settings,
        suppliers_csv=csv_path,
    )
    return orch, settings.patients_json


def run_scenario(
    scenario: str,
    patient_id: str,
    *,
    use_fake_llm: bool = False,
    suppliers_csv: Path | None = None,
) -> int:
    orch, patients_path = build_orchestrator(
        scenario,
        use_fake_llm=use_fake_llm,
        suppliers_csv=suppliers_csv,
    )
    try:
        record = get_patient(patients_path, patient_id)
    except KeyError as exc:
        raise SystemExit(str(exc)) from exc

    print(
        f"patient_id={record.patient.patient_id} name={record.patient.name} "
        f"city={record.patient.city} equipment={record.equipment}"
    )
    print(f"pcp={record.pcp.name} @ {record.pcp.practice}")
    print(f"suppliers_csv={orch.suppliers_csv}\n")

    case = orch.create_case(record, scenario_name=scenario)
    try:
        case = orch.run_until_terminal(case.case_id)
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        msg = str(exc)
        if (
            "insufficient_quota" in msg
            or "429" in msg
            or name in {"RateLimitError", "HTTPStatusError"}
            or "RESOURCE_EXHAUSTED" in msg
        ):
            raise SystemExit(
                "LLM provider rejected the request (quota/rate limit/auth). "
                "Check GEMINI_API_KEY / OPENAI_API_KEY billing, or rerun with --fake-llm."
            ) from exc
        raise

    print("\n=== RUN SUMMARY ===")
    print(f"case_id={case.case_id}")
    print(f"patient_id={case.patient.patient_id}")
    print(f"status={case.status.value}")
    print(f"supplier_contacts={case.supplier_contacts}")
    print(f"pcp_contacts={case.pcp_contacts}")
    print(f"retries={case.retries}")
    print(f"human_intervention_required={case.status == CaseStatus.ESCALATED}")
    if case.selected_supplier_id:
        print(f"selected_supplier_id={case.selected_supplier_id}")
    if case.commitment:
        print(
            f"commitment_date={case.commitment.commitment_date} "
            f"confirmed={bool(case.commitment.confirmed_at)} "
            f"breached={case.commitment.breached}"
        )
    if case.escalation:
        print("escalation=")
        print(json.dumps(case.escalation.model_dump(mode="json"), indent=2))
    print("\n=== EVENTS ===")
    for e in case.events:
        print(f"{e.timestamp.isoformat()} {e.type.value} actor={e.actor}")
    return 0 if case.status in (CaseStatus.COMPLETE, CaseStatus.ESCALATED) else 1


def main(argv: list[str] | None = None) -> None:
    get_settings.cache_clear()
    settings = get_settings()
    known_patients = sorted(load_patients(settings.patients_json))
    known_scenarios = sorted(p.stem for p in settings.scenarios_dir.glob("*.json"))

    parser = argparse.ArgumentParser(description="DME coordination demo")
    parser.add_argument(
        "--scenario",
        required=True,
        choices=known_scenarios,
        help="Scenario behavior file under data/scenarios/",
    )
    parser.add_argument(
        "--patient",
        required=True,
        choices=known_patients,
        help="Patient ID from data/patients.json",
    )
    parser.add_argument(
        "--suppliers",
        type=Path,
        default=None,
        help="Optional path to suppliers CSV (default: data/suppliers.csv)",
    )
    parser.add_argument(
        "--fake-llm",
        action="store_true",
        help="Force FakeLLM even if OPENAI_API_KEY is set",
    )
    args = parser.parse_args(argv)
    raise SystemExit(
        run_scenario(
            args.scenario,
            args.patient,
            use_fake_llm=args.fake_llm,
            suppliers_csv=args.suppliers,
        )
    )


if __name__ == "__main__":
    main()
