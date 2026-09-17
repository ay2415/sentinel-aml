"""Versioned prompt registry. Prompts live in /prompts/*.yaml (reviewed via pull request), not in code.
Every LLM call records prompt name, semantic version and content hash."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from string import Template

import yaml

from app.core.config import get_settings


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    version: str
    model_tier: str
    max_tokens: int
    temperature: float
    system: str
    user: str
    sha256: str

    def render(self, **variables) -> tuple[str, str]:
        return Template(self.system).safe_substitute(**variables), Template(self.user).safe_substitute(**variables)


@lru_cache
def load_prompt(name: str) -> PromptTemplate:
    path = get_settings().prompts_dir / f"{name}.yaml"
    raw = path.read_text(encoding="utf-8")
    d = yaml.safe_load(raw)
    if d.get("name") != name or d.get("model_tier") not in ("fast", "reasoning"):
        raise ValueError(f"invalid prompt file {path}")
    return PromptTemplate(name=name, version=str(d["version"]), model_tier=d["model_tier"], max_tokens=int(d["max_tokens"]),
                          temperature=float(d.get("temperature", 0)), system=d["system"], user=d["user"],
                          sha256=hashlib.sha256(raw.encode()).hexdigest())


def list_prompts() -> list[dict]:
    out = []
    for p in sorted(get_settings().prompts_dir.glob("*.yaml")):
        t = load_prompt(p.stem)
        out.append({"name": t.name, "version": t.version, "model_tier": t.model_tier, "sha256": t.sha256[:12]})
    return out
