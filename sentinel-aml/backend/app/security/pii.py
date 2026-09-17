"""PII detection/redaction applied before data reaches logs or an external LLM.

Customer names never leave the database: agents see pseudonymous customer ids.
Regex redaction is a second line of defence for free text (payment references).
Production: pair with Microsoft Presidio / Azure AI Language PII detection.
"""
from __future__ import annotations

import hashlib
import re

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,4})?\b")),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("PHONE", re.compile(r"(?<!\w)\+?\d{1,3}[\s-]?\(?\d{2,4}\)?[\s-]?\d{3,4}[\s-]?\d{3,4}(?!\w)")),
    ("PPSN", re.compile(r"\b\d{7}[A-W][A-IW]?\b")),  # Irish Personal Public Service Number
]


# System identifiers are not personal data. Regression: a random UUID fragment ("00183659-8165") matched the phone
# pattern, failing ~1.6% of verified recommendations nondeterministically.
_SYSTEM_IDS = re.compile(r"\b(?:ALERT|TX|CP|PROFILE|FEATURE):[A-Za-z0-9_\-\.]+|\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")


def _split_system_ids(text: str) -> tuple[str, list[str]]:
    ids: list[str] = []

    def keep(m):
        ids.append(m.group())
        return f"\x00{len(ids) - 1}\x00"

    return _SYSTEM_IDS.sub(keep, text), ids


def redact_text(text: str | None) -> str:
    if not text:
        return text or ""
    out, ids = _split_system_ids(text)
    for label, pat in _PATTERNS:
        out = pat.sub(f"[{label}_REDACTED]", out)
    return re.sub(r"\x00(\d+)\x00", lambda m: ids[int(m.group(1))], out)


def contains_pii(text: str | None) -> list[str]:
    if not text:
        return []
    masked, _ = _split_system_ids(text)
    return [label for label, pat in _PATTERNS if pat.search(masked)]


def pseudonymise(value: str, salt: str) -> str:
    return "P-" + hashlib.sha256(f"{salt}:{value}".encode()).hexdigest()[:12]


def mask_iban(iban: str) -> str:
    return iban[:4] + "*" * max(0, len(iban) - 8) + iban[-4:] if iban and len(iban) > 8 else "****"
