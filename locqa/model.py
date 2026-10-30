"""Finding / Diagnostic data model. Field names follow the brief's report.json schema."""
from __future__ import annotations

from dataclasses import dataclass, field

ISSUE_TYPES = (
    "untranslated",
    "same_as_source",
    "placeholder_mismatch",
    "plural_mismatch",
    "glossary_violation",
    "length_overflow",
    "machine_translation_suspect",
    "inconsistent_term",
    "suffix_harmony",
    "orthography",
    "wrong_language",
)
SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}


def max_severity(*sevs: str) -> str:
    return max(sevs, key=lambda s: SEVERITY_RANK.get(s, 0))


@dataclass
class Finding:
    key: str
    issue_type: str
    severity: str
    source: str | None
    target: str | None
    evidence: str
    suggestion: str | None = None
    confidence: float = 0.5
    reviewed_by_human: bool = False
    rule: str = ""
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.issue_type not in ISSUE_TYPES:
            raise ValueError(f"unknown issue_type: {self.issue_type}")
        if self.severity not in SEVERITY_RANK:
            raise ValueError(f"unknown severity: {self.severity}")
        self.confidence = round(max(0.0, min(1.0, float(self.confidence))), 2)
        if self.suggestion is not None and self.suggestion == self.target:
            self.suggestion = None  # a "fix" identical to the current text is noise

    def to_dict(self) -> dict:
        d = {
            "key": self.key,
            "issue_type": self.issue_type,
            "severity": self.severity,
            "source": self.source,
            "target": self.target,
            "evidence": self.evidence,
            "suggestion": self.suggestion,
            "confidence": self.confidence,
            "reviewed_by_human": self.reviewed_by_human,
            "rule": self.rule,
        }
        if self.tags:
            d["tags"] = sorted(set(self.tags))
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Finding":
        return cls(
            key=d["key"],
            issue_type=d["issue_type"],
            severity=d.get("severity", "medium"),
            source=d.get("source"),
            target=d.get("target"),
            evidence=d.get("evidence", ""),
            suggestion=d.get("suggestion"),
            confidence=d.get("confidence", 0.5),
            reviewed_by_human=bool(d.get("reviewed_by_human", False)),
            rule=d.get("rule", ""),
            tags=list(d.get("tags", [])),
        )


@dataclass
class Diagnostic:
    """File-level problems that are not one of the 11 issue types (kept out of findings
    so they don't dilute precision): duplicates, orphan metadata, invalid source ICU..."""

    level: str  # error | warning | info
    code: str
    message: str
    key: str | None = None

    def to_dict(self) -> dict:
        d = {"level": self.level, "code": self.code, "message": self.message}
        if self.key is not None:
            d["key"] = self.key
        return d
