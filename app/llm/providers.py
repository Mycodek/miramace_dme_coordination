"""LLM providers: fake (tests/offline), Gemini, OpenAI."""

from __future__ import annotations

import json
from typing import TypeVar

import httpx
from pydantic import BaseModel

from app.entities.models import OrderExtraction, PatientRecord, SupplierFacts
from app.llm.prompts import pcp_extract_prompt, supplier_extract_prompt
from app.policies import expected_hcpcs

T = TypeVar("T", bound=BaseModel)


class FakeLLMClient:
    def __init__(self, record: PatientRecord | None = None) -> None:
        self.record = record

    def bind_patient(self, record: PatientRecord) -> None:
        self.record = record

    def extract_supplier_response(self, transcript: str) -> SupplierFacts:
        t = transcript.lower().strip()
        if not t:
            return SupplierFacts(no_answer=True, confidence=1.0)
        rejects_assignment = (
            "do not accept assignment" in t
            or "not accept assignment" in t
            or "balance bill" in t
        )
        accepts = (
            not rejects_assignment
            and (
                "accept assignment" in t
                or "accepts assignment" in t
                or "20%" in t
                or "coinsurance" in t
            )
        )
        if "not accepting" in t:
            return SupplierFacts(
                accepting_new_patients=False,
                medicare_part_b=True,
                accepts_assignment=accepts or None,
                confidence=1.0,
            )
        if "do not provide" in t or "no k0001" in t:
            return SupplierFacts(
                accepting_new_patients=True,
                medicare_part_b=True,
                k0001_available=False,
                confidence=1.0,
            )
        if "out of stock" in t:
            return SupplierFacts(
                accepting_new_patients=True,
                medicare_part_b=True,
                out_of_stock=True,
                k0001_available=False,
                confidence=1.0,
            )
        if "delivered" in t or "delivery completed" in t:
            return SupplierFacts(
                accepting_new_patients=True,
                medicare_part_b=True,
                k0001_available=True,
                delivery_possible=True,
                delivery_eta_days=0,
                accepts_assignment=True,
                confidence=1.0,
            )
        if "k0001" in t or "wheelchair" in t:
            return SupplierFacts(
                accepting_new_patients=True,
                medicare_part_b=True,
                k0001_available=True,
                delivery_possible=True,
                delivery_eta_days=2,
                accepts_assignment=False if rejects_assignment else True,
                confidence=1.0,
            )
        return SupplierFacts(no_answer=True, confidence=0.5)

    def extract_pcp_response(self, transcript: str) -> OrderExtraction:
        t = transcript.lower().strip()
        if not t:
            return OrderExtraction(no_response=True, order_status="no_response")
        if (
            "verbal only" in t
            or "not yet signed" in t
            or "written order incomplete" in t
        ):
            return OrderExtraction(
                order_status="incomplete",
                signed=False,
                face_to_face=False,
                home_assessment=False,
                no_response=False,
            )
        if "signed" in t or "k0001" in t or "order" in t:
            equip = self.record.equipment if self.record else "standard manual wheelchair"
            face = "face-to-face" in t or "face to face" in t or "signed" in t
            home = "home" in t or "signed" in t
            return OrderExtraction(
                order_status="submitted",
                patient_name=self.record.patient.name if self.record else "Unknown",
                order_date="2026-08-09",
                item_description=equip,
                practitioner_name=self.record.pcp.name if self.record else "Unknown",
                signed=True,
                hcpcs_code=expected_hcpcs(equip) or "K0001",
                no_response=False,
                face_to_face=face,
                home_assessment=home,
            )
        return OrderExtraction(order_status="incomplete", signed=False)


class GeminiLLMClient:
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def _extract(self, system: str, transcript: str) -> dict:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [
                {"role": "user", "parts": [{"text": f"Transcript:\n{transcript}"}]}
            ],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
            },
        }
        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "X-goog-api-key": self.api_key,
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        return json.loads(data["candidates"][0]["content"]["parts"][0]["text"])

    def _extract_model(self, model: type[T], prompt: str, transcript: str) -> T:
        return model.model_validate(self._extract(prompt, transcript))

    def extract_supplier_response(self, transcript: str) -> SupplierFacts:
        if not transcript.strip():
            return SupplierFacts(no_answer=True, confidence=1.0)
        return self._extract_model(
            SupplierFacts, supplier_extract_prompt(), transcript
        )

    def extract_pcp_response(self, transcript: str) -> OrderExtraction:
        if not transcript.strip():
            return OrderExtraction(no_response=True, order_status="no_response")
        return self._extract_model(OrderExtraction, pcp_extract_prompt(), transcript)


class OpenAILLMClient:
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def _extract(self, system: str, transcript: str) -> dict:
        from openai import OpenAI

        response = OpenAI(api_key=self.api_key).chat.completions.create(
            model=self.model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": transcript},
            ],
        )
        return json.loads(response.choices[0].message.content or "{}")

    def _extract_model(self, model: type[T], prompt: str, transcript: str) -> T:
        return model.model_validate(self._extract(prompt, transcript))

    def extract_supplier_response(self, transcript: str) -> SupplierFacts:
        if not transcript.strip():
            return SupplierFacts(no_answer=True, confidence=1.0)
        return self._extract_model(
            SupplierFacts, supplier_extract_prompt(), transcript
        )

    def extract_pcp_response(self, transcript: str) -> OrderExtraction:
        if not transcript.strip():
            return OrderExtraction(no_response=True, order_status="no_response")
        return self._extract_model(OrderExtraction, pcp_extract_prompt(), transcript)
