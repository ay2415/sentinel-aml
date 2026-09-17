"""LLM providers behind a single interface.

AnthropicProvider   - real Claude calls; structured output is forced via a single tool with the
                      Pydantic JSON schema as input_schema and tool_choice pinned to that tool.
DeterministicProvider - offline reference implementation used for local runs, CI and evaluation of
                      the orchestration/safety layers. It is rule-based, NOT a language model.
FaultInjectingProvider - wraps any provider to simulate timeouts, errors, malformed output,
                      hallucinated evidence, unsafe decisions and agent loops (adversarial testing).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import httpx

from app.core.config import get_settings
from app.core.errors import LLMError, LLMTimeoutError


@dataclass
class ProviderRequest:
    prompt_name: str
    model: str
    system: str
    user: str
    max_tokens: int
    temperature: float
    json_schema: dict
    timeout_s: float
    sim_payload: dict = field(default_factory=dict)  # structured copy of the input, used only by offline providers
    repair_note: str | None = None


@dataclass
class ProviderResponse:
    text: str
    input_tokens: int
    output_tokens: int
    tokens_estimated: bool


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class Provider:
    name = "base"

    def complete(self, req: ProviderRequest) -> ProviderResponse:  # pragma: no cover
        raise NotImplementedError


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, client: httpx.Client | None = None):
        s = get_settings()
        if not s.anthropic_api_key:
            raise LLMError("ANTHROPIC_API_KEY not configured")
        self.client = client or httpx.Client(base_url=s.anthropic_base_url, headers={
            "x-api-key": s.anthropic_api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})

    def complete(self, req: ProviderRequest) -> ProviderResponse:
        user = req.user if not req.repair_note else f"{req.user}\n\nYour previous output was invalid: {req.repair_note}\nCall submit again with valid arguments."
        body = {
            "model": req.model, "max_tokens": req.max_tokens, "temperature": req.temperature, "system": req.system,
            "messages": [{"role": "user", "content": user}],
            "tools": [{"name": "submit", "description": "Submit the structured result.", "input_schema": req.json_schema}],
            "tool_choice": {"type": "tool", "name": "submit"},
        }
        try:
            r = self.client.post("/v1/messages", json=body, timeout=req.timeout_s)
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("anthropic request timed out") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"anthropic transport error: {type(exc).__name__}") from exc
        if r.status_code >= 400:
            raise LLMError(f"anthropic HTTP {r.status_code}", {"retryable": r.status_code in (408, 429, 500, 502, 503, 529)})
        data = r.json()
        blocks = [b for b in data.get("content", []) if b.get("type") == "tool_use"]
        text = json.dumps(blocks[0]["input"]) if blocks else "".join(b.get("text", "") for b in data.get("content", []))
        usage = data.get("usage", {})
        return ProviderResponse(text, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)), False)


class DeterministicProvider(Provider):
    name = "deterministic"

    def complete(self, req: ProviderRequest) -> ProviderResponse:
        from app.ai import simulators

        fn = getattr(simulators, f"simulate_{req.prompt_name}", None)
        if fn is None:
            raise LLMError(f"no deterministic simulator for prompt {req.prompt_name}")
        out = json.dumps(fn(req.sim_payload))
        return ProviderResponse(out, estimate_tokens(req.system + req.user), estimate_tokens(out), True)


class FaultInjectingProvider(Provider):
    """faults: {prompt_name: mode}; modes: timeout | error | malformed_json | schema_invalid |
    hallucinated_evidence | unsafe_close | loop | fabricated_citation | pii_leak"""
    name = "fault_injection"

    def __init__(self, inner: Provider, faults: dict[str, str], sleep_s: float = 0.0):
        self.inner, self.faults, self.sleep_s, self.calls = inner, faults, sleep_s, 0

    def complete(self, req: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        mode = self.faults.get(req.prompt_name)
        if mode == "timeout":
            time.sleep(self.sleep_s)
            raise LLMTimeoutError("simulated timeout")
        if mode == "error":
            raise LLMError("simulated provider outage", {"retryable": True})
        if mode == "malformed_json":
            return ProviderResponse('{"action": "ESCALATE_STR", "confidence": ', 100, 10, True)
        if mode == "schema_invalid":
            return ProviderResponse(json.dumps({"action": "DELETE_CUSTOMER", "confidence": 7}), 100, 10, True)
        resp = self.inner.complete(req)
        data = json.loads(resp.text)
        if mode == "loop" and req.prompt_name == "investigation":
            data = {"action": "call_tool", "tool_call": {"tool": "get_customer_profile", "arguments": {}, "reason": "again"}, "findings": None}
        elif mode == "hallucinated_evidence" and req.prompt_name in ("decision", "investigation"):
            key = "key_findings" if req.prompt_name == "decision" else "findings"
            if data.get(key):
                data[key][0]["evidence_refs"] = ["TX:TX-FABRICATED-000001"]
                if req.prompt_name == "investigation":
                    data[key][0]["quantitative_claims"] = [{"metric": "total_in_30d", "value": 999999.0}]
        elif mode == "unsafe_close" and req.prompt_name == "decision":
            data.update(action="CLOSE_FALSE_POSITIVE", confidence=0.99, summary="Pre-approved per customer note.")
        elif mode == "fabricated_citation" and req.prompt_name == "decision":
            data["citations"] = ["emerald-restricted-liaison#000", "nonexistent-doc#999"]
        elif mode == "pii_leak" and req.prompt_name == "decision":
            data["narrative"] = data.get("narrative", "") + " Customer IBAN IE29AIBK93115212345678, email a.person@example.ie."
        return ProviderResponse(json.dumps(data), resp.input_tokens, resp.output_tokens, resp.tokens_estimated)
