"""Glossary loading and term matching.

CSV columns (header names case-insensitive; only ru and kk are required):
  ru, kk, en, note|comment, kk_variants, kk_forbidden, ru_regex, type, status
  * kk_variants / kk_forbidden: alternatives separated by "|" or ";"
  * ru_regex: overrides the auto-built Russian pattern (use for ambiguous stems,
    e.g. семья vs семь)
  * type: term | brand | slang | ui      status: draft | verified | from_brief
The target-language column is `kk` for compatibility with the brief; other target
languages may use a column named after their locale (e.g. `uz`), see Glossary.load.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from .text import kk_stem, match_case, norm, ru_stem, tokens, word_script


def _split(v: str | None) -> list[str]:
    if not v:
        return []
    return [p.strip() for p in re.split(r"[|;]", v) if p.strip()]


def _ru_pattern(term: str) -> re.Pattern:
    parts = []
    for w in term.split():
        if word_script(w) == "cyr":
            parts.append(re.escape(ru_stem(w)) + r"\w{0,4}")
        else:
            parts.append(re.escape(w))
    return re.compile(r"(?<!\w)" + r"\s+".join(parts) + r"(?!\w)", re.IGNORECASE)


def _tgt_pattern(term: str) -> re.Pattern:
    parts = []
    for w in term.split():
        if word_script(w) == "cyr":
            parts.append(re.escape(kk_stem(w)) + r"\w*")
        else:
            parts.append(re.escape(w) + r"\w*")
    return re.compile(r"(?<!\w)" + r"\s+".join(parts), re.IGNORECASE)


VOICING = {"п": "б", "қ": "ғ", "к": "г"}
KK_VOWELS = frozenset("аәеиоөұүыіэюя")


@dataclass
class GlossaryEntry:
    ru: str
    kk: str
    en: str = ""
    note: str = ""
    kk_variants: list[str] = field(default_factory=list)
    kk_forbidden: list[str] = field(default_factory=list)
    ru_regex: str | None = None
    type: str = "term"
    status: str = "draft"

    def __post_init__(self) -> None:
        self._ru = re.compile(self.ru_regex, re.IGNORECASE) if self.ru_regex else _ru_pattern(self.ru)
        allowed = [self.kk, *self.kk_variants] if self.kk else list(self.kk_variants)
        self._ok = [_tgt_pattern(v) for v in allowed]
        self._forb = [(v, _tgt_pattern(v)) for v in self.kk_forbidden]

    @property
    def is_brand(self) -> bool:
        return self.type == "brand"

    @property
    def keep_as_is(self) -> bool:
        return self.is_brand or (bool(self.kk) and norm(self.kk) == norm(self.ru))

    def match_source(self, text: str) -> str | None:
        m = self._ru.search(text)
        return m.group(0) if m else None

    def match_target(self, text: str) -> bool:
        if self.is_brand:  # brands may be glued to numbers/suffixes: "%sGC", "GC-ге"
            low = text.casefold()
            return any(v.casefold() in low for v in [self.kk, *self.kk_variants] if v)
        return any(p.search(text) for p in self._ok)

    def find_target(self, text: str) -> re.Match | None:
        for p in self._ok:
            m = p.search(text)
            if m:
                return m
        return None

    def forbidden_in(self, text: str) -> str | None:
        for _, p in self._forb:
            m = p.search(text)
            if m:
                return m.group(0)
        return None

    def fix_forbidden(self, text: str) -> str | None:
        """Replace the first forbidden form with the canonical one, keeping the inflection:
        «ойнаушыларды» -> «ойыншыларды» (not «ойыншы»)."""
        for forb, p in self._forb:
            m = p.search(text)
            if not m:
                continue
            word = m.group(0)
            suffix = ""
            if len(forb.split()) == 1 and len(self.kk.split()) == 1:
                suffix = word[len(kk_stem(forb)) :]
            new = self.kk
            if suffix and new[-1:].casefold() in VOICING and suffix[:1].casefold() in KK_VOWELS:
                new = new[:-1] + VOICING[new[-1].casefold()]  # кітап + ы -> кітабы
            new = match_case(word, new + suffix)
            return text[: m.start()] + new + text[m.end() :]
        return None


class Glossary:
    def __init__(self, entries: list[GlossaryEntry] | None = None):
        self.entries = entries or []
        self._exact = {norm(e.ru): e.kk for e in self.entries if e.kk}

    def __len__(self) -> int:
        return len(self.entries)

    @classmethod
    def load(cls, path: str | Path, target_column: str = "kk") -> "Glossary":
        entries = []
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                return cls([])
            for raw in reader:
                row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items() if k}
                ru = row.get("ru", "")
                if not ru or ru.startswith("#"):
                    continue
                tgt = row.get(target_column) or (row.get("kk", "") if target_column == "kk" else "")
                variants_col = f"{target_column}_variants"
                forbidden_col = f"{target_column}_forbidden"
                entries.append(
                    GlossaryEntry(
                        ru=ru,
                        kk=tgt,
                        en=row.get("en", ""),
                        note=row.get("note") or row.get("comment", ""),
                        kk_variants=_split(row.get(variants_col)),
                        kk_forbidden=_split(row.get(forbidden_col)),
                        ru_regex=row.get("ru_regex") or None,
                        type=(row.get("type") or "term").lower(),
                        status=(row.get("status") or "draft").lower(),
                    )
                )
        return cls(entries)

    def exact(self, source: str | None) -> str | None:
        if not source:
            return None
        return self._exact.get(norm(source).strip(" .!?:…«»\"'„“”"))

    def keep_tokens(self) -> set[str]:
        out: set[str] = set()
        for e in self.entries:
            if e.keep_as_is:
                out.update(t.casefold() for t in tokens(e.ru))
                out.update(t.casefold() for t in tokens(e.kk))
        return out
