"""General extract prompt templates. Output shape comes from Pydantic schemas."""

from __future__ import annotations

from app.entities.models import ExtractSchema, OrderExtraction, SupplierFacts

EXTRACT_PROMPT_TEMPLATE = """
You are extracting structured facts from {source}.

Rules:
- Do not infer facts that are not stated.
- If unknown, return null.
- Return a single JSON object that matches the schema below.

Output JSON schema:
{schema}
""".strip()

SUPPLIER_SOURCE = "a supplier phone transcript"
PCP_SOURCE = "a PCP office response"


def build_extract_prompt(source: str, schema: str) -> str:
    return EXTRACT_PROMPT_TEMPLATE.format(source=source, schema=schema)


def prompt_for(model: type[ExtractSchema], source: str) -> str:
    return build_extract_prompt(source, model.to_json())


def supplier_extract_prompt() -> str:
    return prompt_for(SupplierFacts, SUPPLIER_SOURCE)


def pcp_extract_prompt() -> str:
    return prompt_for(OrderExtraction, PCP_SOURCE)
