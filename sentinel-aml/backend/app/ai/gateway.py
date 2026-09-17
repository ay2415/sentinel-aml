"""LLM gateway: model routing, retries with schema repair, timeouts, circuit breaking, cost and
lineage recording. Agents never talk to a provider directly.

Production: this module sits behind an internal model gateway (e.g. Azure API Management or LiteLLM
proxy) that adds per-tenant quotas, key rotation, regional routing and response caching.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.ai.prompts import load_prompt
from app.ai.providers import AnthropicProvider, DeterministicProvider, Provider, ProviderRequest
from app.core.config import get_settings
from app.core.errors import CircuitOpenError, LLMError, LLMTimeoutError
from app.core.logging import log_event
from app.database.models import LLMCall
from app.observability import metrics as m
from app.security.pii import redact_text

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class CircuitBreaker:
    def __init__(self, threshold: int, reset_seconds: float):
        self.threshold, self.reset_seconds = threshold, reset_seconds
        self.failures: dict[str, int] = {}
        self.opened_at: dict[str, float] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        with self._lock:
            opened = self.opened_at.get(key)
            if opened is not None:
                if time.monotonic() - opened < self.reset_seconds:
                    raise CircuitOpenError(f"circuit open for {key}")
                self.opened_at.pop(key, None)  # half-open: allow one trial call
                self.failures[key] = self.threshold - 1

    def success(self, key: str) -> None:
        with self._lock:
            self.failures[key] = 0

    def failure(self, key: str) -> None:
        with self._lock:
            self.failures[key] = self.failures.get(key, 0) + 1
            if self.failures[key] >= self.threshold:
                self.opened_at[key] = time.monotonic()


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price = get_settings().model_pricing.get(model)
    if not price:
        return 0.0
    return round(input_tokens / 1e6 * price["input"] + output_tokens / 1e6 * price["output"], 6)


def extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError("top-level JSON must be an object")
    return obj


class LLMGateway:
    def __init__(self, provider: Provider | None = None):
        s = get_settings()
        if provider is None:
            provider = AnthropicProvider() if s.llm_provider == "anthropic" else DeterministicProvider()
        self.provider = provider
        self.breaker = CircuitBreaker(s.circuit_breaker_threshold, s.circuit_breaker_reset_seconds)

    def model_for(self, tier: str) -> str:
        s = get_settings()
        return s.model_reasoning if tier == "reasoning" else s.model_fast

    def structured(self, db: Session, *, prompt_name: str, schema: type[T], variables: dict, sim_payload: dict,
                   agent_name: str, workflow_id: str | None, tenant_id: str | None,
                   retrieved_chunk_ids: list[str] | None = None) -> tuple[T, dict]:
        s = get_settings()
        tpl = load_prompt(prompt_name)
        model = self.model_for(tpl.model_tier)
        system, user = tpl.render(**variables)
        req = ProviderRequest(prompt_name, model, system, user, tpl.max_tokens, tpl.temperature, schema.model_json_schema(),
                              s.llm_timeout_seconds, sim_payload)
        t0 = time.perf_counter()
        attempts, tokens_in, tokens_out, estimated = 0, 0, 0, False
        status, error, parsed, output_text = "error", None, None, None
        try:
            self.breaker.check(model)
            for attempt in range(s.llm_max_retries + 1):
                attempts += 1
                try:
                    resp = self.provider.complete(req)
                except LLMTimeoutError as exc:
                    status, error = "timeout", str(exc)
                    self.breaker.failure(model)
                    continue
                except LLMError as exc:
                    status, error = "error", exc.message
                    self.breaker.failure(model)
                    if not exc.details.get("retryable", False):
                        break
                    time.sleep(min(2.0, 0.2 * (2 ** attempt)))  # exponential backoff
                    continue
                tokens_in += resp.input_tokens
                tokens_out += resp.output_tokens
                estimated = estimated or resp.tokens_estimated
                output_text = resp.text
                try:
                    parsed = schema.model_validate(extract_json(resp.text))
                    status, error = "ok", None
                    self.breaker.success(model)
                    break
                except (ValueError, ValidationError) as exc:  # json.JSONDecodeError is a ValueError
                    status, error = "schema_invalid", str(exc)[:500]
                    req.repair_note = str(exc)[:800]
        except CircuitOpenError as exc:
            status, error = "circuit_open", exc.message
        latency_ms = (time.perf_counter() - t0) * 1000
        cost = cost_usd(model, tokens_in, tokens_out)
        call = LLMCall(tenant_id=tenant_id, workflow_id=workflow_id, agent_name=agent_name, provider=self.provider.name, model=model,
                       prompt_name=tpl.name, prompt_version=tpl.version, prompt_sha256=tpl.sha256, input_tokens=tokens_in,
                       output_tokens=tokens_out, tokens_estimated=estimated, cost_usd=cost, latency_ms=latency_ms, attempts=attempts,
                       status=status, schema_valid=parsed is not None, retrieved_chunk_ids=retrieved_chunk_ids or [],
                       output_preview=redact_text(output_text)[:2000] if output_text else None, error=error)
        db.add(call)
        db.flush()
        m.LLM_CALLS.labels(model=model, prompt=tpl.name, status=status).inc()
        m.LLM_TOKENS.labels(model=model, direction="input").inc(tokens_in)
        m.LLM_TOKENS.labels(model=model, direction="output").inc(tokens_out)
        m.LLM_COST.labels(model=model).inc(cost)
        m.LLM_LATENCY.labels(model=model).observe(latency_ms / 1000)
        log_event(log, "llm_call", agent=agent_name, model=model, prompt=f"{tpl.name}@{tpl.version}", status=status,
                  attempts=attempts, latency_ms=round(latency_ms, 1), tokens=tokens_in + tokens_out, cost_usd=cost)
        if parsed is None:
            raise LLMError(f"{agent_name}: LLM call failed ({status})", {"status": status, "llm_call_id": call.id})
        return parsed, {"llm_call_id": call.id, "tokens": tokens_in + tokens_out, "cost_usd": cost, "model": model,
                        "prompt_version": tpl.version, "latency_ms": latency_ms}
