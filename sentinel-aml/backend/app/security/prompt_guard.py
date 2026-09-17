"""Prompt-injection defences.

Layered model (no single layer is trusted on its own):
 1. Detection   - heuristics flag instruction-like text in untrusted data (payment references,
                  retrieved documents, uploaded KB files).
 2. Isolation   - untrusted data is wrapped in <untrusted_data> tags ("spotlighting") and the
                  system prompt states it is data, never instructions.
 3. Containment - the LLM cannot take actions: it emits a schema-constrained recommendation,
                  tools are read-only and allow-listed, a deterministic policy engine decides
                  approvals, and any injection flag forces human review.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# (name, severity, pattern). HIGH = attempts to control the model -> always blocking.
# MEDIUM = decision-steering language: normal inside policy documents ("do not close an alert as a
# false positive when..."), but anomalous inside customer-controlled free text such as payment references.
_RULES: list[tuple[str, str, re.Pattern]] = [
    ("override_instructions", "high", re.compile(r"\b(ignore|disregard|forget|override)\b.{0,40}\b(previous|prior|above|all|earlier|system)\b.{0,20}\b(instruction|prompt|rule|direction)s?", re.I | re.S)),
    ("role_hijack", "high", re.compile(r"\b(you are now|act as|pretend to be|new instructions|system prompt|developer mode|approval mode)\b", re.I)),
    ("ai_directive", "high", re.compile(r"\b(AI|LLM|assistant|chatbot|bot|agents?|reviewers?)\b[\w\s]{0,20}\b(must|should|shall|are instructed to|need to)\s+(now\s+)?(ignore|close|approve|clear|mark|disregard|reveal|never|not (escalate|report|flag))", re.I)),
    ("tag_injection", "high", re.compile(r"</?\s*(system|untrusted_data|instructions|assistant|tool_result|source)\b", re.I)),
    ("exfiltration", "high", re.compile(r"\b(reveal|print|output|send|leak)\b.{0,30}\b(system prompt|api key|password|secret|credentials)\b", re.I | re.S)),
    ("decision_manipulation", "medium", re.compile(r"\b(mark|classify|treat|close|clear)\b.{0,30}\b(as )?(legitimate|false positive|not suspicious|pre-?approved|cleared)\b", re.I | re.S)),
    ("suppress_reporting", "medium", re.compile(r"\b(do not|don't|never|must not)\b.{0,15}\b(report|escalate|file an? (str|report))\b", re.I | re.S)),
]

_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)


@dataclass
class InjectionFinding:
    rule: str
    severity: str
    excerpt: str


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_ZERO_WIDTH)
    return "".join(ch for ch in text if ch.isprintable() or ch in "\n\t")


def scan(text: str | None, context: str = "free_text") -> list[InjectionFinding]:
    """context='free_text' (payment references, queries): all rules.
    context='document' (policy/KB content): high-severity rules only."""
    if not text:
        return []
    t = normalise(text)
    findings = []
    for name, severity, pat in _RULES:
        if context == "document" and severity != "high":
            continue
        m = pat.search(t)
        if m:
            findings.append(InjectionFinding(rule=name, severity=severity, excerpt=t[max(0, m.start() - 10): m.end() + 10][:120]))
    return findings


def neutralise(text: str | None) -> str:
    """Normalise and defang delimiter look-alikes so data cannot close our wrapper tags."""
    if not text:
        return ""
    return normalise(text).replace("<", "‹").replace(">", "›")


def wrap_untrusted(label: str, content: str) -> str:
    return f'<untrusted_data source="{label}">\n{neutralise(content)}\n</untrusted_data>'
