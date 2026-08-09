"""Policy norms + assessors.

Add a new Norms class + assess_* helpers per use case (patient eligibility,
supplier criteria, PCP paperwork, treatment, etc.) without crowding one blob.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.entities.models import PatientEligibility

POLICY_VERSION = "medicare-k0001-v1"


class CoverageNorms:
    """Cross-cutting Medicare DME coverage constants."""

    MEDICARE_PAYS_PCT = 80
    PATIENT_COINSURANCE_PCT = 20
    CAPPED_RENTAL_MONTHS = 13
    HCPCS_K0001 = "K0001"
    REQUIRED_HCPCS: dict[str, str] = {
        "standard manual wheelchair": HCPCS_K0001,
    }


class PatientNorms:
    """Patient medical-necessity criteria (K0001 in-home mobility)."""

    MEDICARE_PART_B_REQUIRED = True
    MAX_WEIGHT_LBS_K0001 = 250


class OrderNorms:
    """PCP / Standard Written Order paperwork criteria."""

    WRITTEN_ORDER_REQUIRED = True
    REQUIRE_FACE_TO_FACE = True
    REQUIRE_HOME_ASSESSMENT = True


class SupplierNorms:
    """DME supplier enrollment / assignment criteria."""

    MEDICARE_PART_B_REQUIRED = True
    REQUIRES_ACCEPTS_ASSIGNMENT = True


# --- Convenience aliases used by helpers / callers ---
MEDICARE_PART_B_REQUIRED = SupplierNorms.MEDICARE_PART_B_REQUIRED
REQUIRES_ACCEPTS_ASSIGNMENT = SupplierNorms.REQUIRES_ACCEPTS_ASSIGNMENT
WRITTEN_ORDER_REQUIRED = OrderNorms.WRITTEN_ORDER_REQUIRED
REQUIRE_FACE_TO_FACE = OrderNorms.REQUIRE_FACE_TO_FACE
REQUIRE_HOME_ASSESSMENT = OrderNorms.REQUIRE_HOME_ASSESSMENT
PATIENT_COINSURANCE_PCT = CoverageNorms.PATIENT_COINSURANCE_PCT
REQUIRED_HCPCS = CoverageNorms.REQUIRED_HCPCS


def expected_hcpcs(equipment: str) -> str | None:
    return CoverageNorms.REQUIRED_HCPCS.get(equipment.strip().lower())


def assess_patient_eligibility(
    eligibility: PatientEligibility,
) -> tuple[bool, list[str]]:
    """Patient-side K0001 medical necessity (extend with more clinical gates later)."""
    errors: list[str] = []
    if PatientNorms.MEDICARE_PART_B_REQUIRED and not eligibility.medicare_part_b:
        errors.append("Original Medicare Part B required")
    if (
        eligibility.weight_lbs is not None
        and eligibility.weight_lbs > PatientNorms.MAX_WEIGHT_LBS_K0001
    ):
        errors.append(
            f"weight exceeds K0001 limit ({PatientNorms.MAX_WEIGHT_LBS_K0001} lbs)"
        )
    if not eligibility.in_home_mradl_limitation:
        errors.append("in-home MRADL mobility limitation required")
    if not eligibility.lesser_device_insufficient:
        errors.append("cane/walker must be insufficient")
    if not eligibility.can_self_propel_or_has_caregiver:
        errors.append("self-propel capability or willing caregiver required")
    if not eligibility.home_accessible:
        errors.append("home must accommodate wheelchair")
    if eligibility.outdoor_use_only:
        errors.append("outdoor-only use does not qualify")
    if eligibility.leisure_only:
        errors.append("leisure/recreation-only use does not qualify")
    if eligibility.backup_device_only:
        errors.append("backup-only device does not qualify")
    return len(errors) == 0, errors


# Future extension points (keep signatures stable when fleshed out):
# def assess_supplier_criteria(...) -> tuple[bool, list[str]]: ...
# def assess_pcp_criteria(...) -> tuple[bool, list[str]]: ...
# def assess_treatment_criteria(...) -> tuple[bool, list[str]]: ...
