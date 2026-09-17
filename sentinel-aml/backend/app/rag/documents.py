"""Markdown + front-matter parsing and structure-aware chunking."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

import yaml

REQUIRED_META = {"doc_id", "title", "doc_type", "classification", "source"}
ALLOWED_CLASSIFICATIONS = {"public", "internal", "confidential", "restricted"}
TARGET_WORDS, OVERLAP_WORDS, MIN_WORDS = 180, 30, 25


@dataclass
class ParsedDocument:
    meta: dict
    body: str
    sha256: str


@dataclass
class Chunk:
    index: int
    section: str
    text: str
    words: int = field(default=0)


class DocumentFormatError(ValueError):
    pass


def parse_markdown(raw: str) -> ParsedDocument:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", raw, re.S)
    if not m:
        raise DocumentFormatError("missing YAML front matter")
    try:
        meta = yaml.safe_load(m.group(1)) or {}  # safe_load: no arbitrary object construction
    except yaml.YAMLError as exc:
        raise DocumentFormatError(f"invalid front matter: {exc}") from exc
    missing = REQUIRED_META - set(meta)
    if missing:
        raise DocumentFormatError(f"front matter missing: {sorted(missing)}")
    if meta["classification"] not in ALLOWED_CLASSIFICATIONS:
        raise DocumentFormatError("invalid classification")
    if not re.fullmatch(r"[a-z0-9][a-z0-9\-]{2,99}", str(meta["doc_id"])):
        raise DocumentFormatError("doc_id must be lowercase slug")
    body = clean_text(m.group(2))
    return ParsedDocument(meta=meta, body=body, sha256=hashlib.sha256(raw.encode()).hexdigest())


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)          # hidden HTML comments are a classic poisoning vector
    text = re.sub(r"[\u200b-\u200d\u2060\ufeff]", "", text)     # zero-width characters
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def chunk_document(doc: ParsedDocument) -> list[Chunk]:
    """Split on headings first (semantic units), then pack paragraphs to ~180 words with overlap.
    Each chunk carries 'Title > Section' so it is self-describing when retrieved in isolation."""
    title = doc.meta["title"]
    sections: list[tuple[str, list[str]]] = []
    current, paras, buf = title, [], []

    def flush_para():
        if buf:
            paras.append(" ".join(buf))
            buf.clear()

    for line in doc.body.split("\n"):  # line-based so headings are detected even without surrounding blank lines
        h = re.match(r"^(#{1,6})\s+(.*)", line.strip())
        if h:
            flush_para()
            if paras:
                sections.append((current, paras))
            current, paras = h.group(2).strip(), []
        elif not line.strip():
            flush_para()
        else:
            buf.append(line.strip())
    flush_para()
    if paras:
        sections.append((current, paras))

    chunks: list[Chunk] = []
    for section, ps in sections:
        words_all: list[list[str]] = []
        for p in ps:  # split oversized paragraphs into windows so no chunk exceeds the budget
            w = p.split()
            for i in range(0, len(w), TARGET_WORDS):
                words_all.append(w[i:i + TARGET_WORDS])
        cur: list[str] = []
        for words in words_all:
            if cur and len(cur) + len(words) > TARGET_WORDS:
                chunks.append(Chunk(len(chunks), section, " ".join(cur)))
                cur = cur[-OVERLAP_WORDS:]
            cur.extend(words)
        if cur and (len(cur) >= MIN_WORDS or not chunks or chunks[-1].section != section):
            chunks.append(Chunk(len(chunks), section, " ".join(cur)))
        elif cur:
            chunks[-1].text += " " + " ".join(cur)
    for c in chunks:
        c.text = f"{title} > {c.section}\n{c.text}"
        c.words = len(c.text.split())
    return chunks
