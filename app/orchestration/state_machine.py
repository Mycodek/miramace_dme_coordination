"""Allowed case status transitions (extend here as lanes grow)."""

from __future__ import annotations

from app.entities.enums import CaseStatus

# Parallel PCP/supplier lanes may interleave — keep permissive during IN_PROGRESS work.
ALLOWED: dict[CaseStatus, set[CaseStatus]] = {
    CaseStatus.CREATED: {CaseStatus.IN_PROGRESS},
    CaseStatus.IN_PROGRESS: {
        CaseStatus.PCP_ORDER_PENDING,
        CaseStatus.SUPPLIER_SEARCH,
        CaseStatus.ORDER_VALIDATED,
        CaseStatus.SUPPLIER_QUALIFIED,
        CaseStatus.READY_TO_MATCH,
        CaseStatus.ESCALATED,
    },
    CaseStatus.PCP_ORDER_PENDING: {
        CaseStatus.ORDER_VALIDATED,
        CaseStatus.SUPPLIER_SEARCH,
        CaseStatus.SUPPLIER_QUALIFIED,
        CaseStatus.ESCALATED,
        CaseStatus.IN_PROGRESS,
    },
    CaseStatus.ORDER_VALIDATED: {
        CaseStatus.SUPPLIER_SEARCH,
        CaseStatus.SUPPLIER_QUALIFIED,
        CaseStatus.READY_TO_MATCH,
        CaseStatus.DELIVERY_COMMITTED,
    },
    CaseStatus.SUPPLIER_SEARCH: {
        CaseStatus.SUPPLIER_QUALIFIED,
        CaseStatus.PCP_ORDER_PENDING,
        CaseStatus.ORDER_VALIDATED,
        CaseStatus.ESCALATED,
        CaseStatus.READY_TO_MATCH,
    },
    CaseStatus.SUPPLIER_QUALIFIED: {
        CaseStatus.READY_TO_MATCH,
        CaseStatus.ORDER_VALIDATED,
        CaseStatus.DELIVERY_COMMITTED,
        CaseStatus.PCP_ORDER_PENDING,
    },
    CaseStatus.READY_TO_MATCH: {CaseStatus.DELIVERY_COMMITTED, CaseStatus.ESCALATED},
    CaseStatus.DELIVERY_COMMITTED: {
        CaseStatus.DELIVERY_CONFIRMED,
        CaseStatus.ESCALATED,
        CaseStatus.SUPPLIER_SEARCH,
        CaseStatus.READY_TO_MATCH,
    },
    CaseStatus.DELIVERY_CONFIRMED: {CaseStatus.COMPLETE},
    CaseStatus.COMPLETE: set(),
    CaseStatus.ESCALATED: set(),
}


def can_transition(current: CaseStatus, new: CaseStatus) -> bool:
    return current == new or new in ALLOWED.get(current, set())


def transition(current: CaseStatus, new: CaseStatus) -> CaseStatus:
    if not can_transition(current, new):
        raise ValueError(f"invalid transition {current} -> {new}")
    return new
