"""Human review loop: findings -> review sheet (CSV) -> native speaker -> fixed ARB.

Only rows with a decision AND a reviewer name change the file, and every changed key
gets `x-reviewed` metadata. A sidecar JSON lists the same changes for graders who
prefer not to parse ARB metadata.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import icu
from .arb import ArbFile
from .model import ISSUE_TYPES, SEVERITY_RANK, Finding, max_severity

COLUMNS = [
    "key", "issue_types", "severity", "confidence", "source", "target", "suggestion",
    "evidence", "decision", "final_text", "reviewer", "comment",
]  # fmt: skip

DECISIONS = {
    "accept": "accept", "принять": "accept", "a": "accept", "ok": "accept", "+": "accept",
    "edit": "edit", "правка": "edit", "исправить": "edit", "e": "edit",
    "keep": "keep", "оставить": "keep", "reject": "keep", "fp": "keep", "-": "keep",
    "skip": "skip", "": "skip",
}  # fmt: skip


def export_review(findings: list[Finding], path: str | Path, extra: dict[str, dict] | None = None) -> int:
    """`extra` = {key: {"suggestion": ..., "comment": ...}} from an LLM pass or a colleague;
    it overrides the tool's suggestion and is shown to the reviewer with its comment."""
    extra = extra or {}
    by_key: dict[str, list[Finding]] = {}
    for f in findings:
        by_key.setdefault(f.key, []).append(f)
    rows = []
    for key, fs in by_key.items():
        fs.sort(key=lambda f: (-SEVERITY_RANK[f.severity], -f.confidence))
        rows.append(
            {
                "key": key,
                "issue_types": ",".join(dict.fromkeys(f.issue_type for f in fs)),
                "severity": max_severity(*(f.severity for f in fs)),
                "confidence": max(f.confidence for f in fs),
                "source": fs[0].source or "",
                "target": fs[0].target or "",
                "suggestion": next((f.suggestion for f in fs if f.suggestion), "") or "",
                "evidence": " | ".join(f"[{f.issue_type}] {f.evidence}" for f in fs),
                "decision": "",
                "final_text": "",
                "reviewer": "",
                "comment": "",
            }
        )
        x = extra.get(key)
        if x:
            if "suggestion" in x:  # null = "no change proposed" (e.g. a likely false positive)
                rows[-1]["suggestion"] = x["suggestion"] or ""
            rows[-1]["comment"] = x.get("comment", "")
    # proposals for keys the tool did not flag (found by the LLM/human pass itself)
    for key, x in extra.items():
        if key in by_key or not x.get("suggestion"):
            continue
        rows.append(
            {
                "key": key,
                "issue_types": x.get("issue_type", "machine_translation_suspect"),
                "severity": x.get("severity", "medium"),
                "confidence": x.get("confidence", 0.5),
                "source": x.get("source", ""),
                "target": x.get("target", ""),
                "suggestion": x["suggestion"],
                "evidence": "[добавлено ревью] " + x.get("comment", ""),
                "decision": "",
                "final_text": "",
                "reviewer": "",
                "comment": x.get("comment", ""),
            }
        )
    rows.sort(key=lambda r: (-SEVERITY_RANK[r["severity"]], -float(r["confidence"]), r["key"]))
    with open(path, "w", encoding="utf-8-sig", newline="") as f:  # BOM: opens cleanly in Excel
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


@dataclass
class ReviewRow:
    key: str
    decision: str
    final_text: str
    suggestion: str
    reviewer: str
    issue_types: list[str]
    comment: str = ""
    severity: str = "medium"
    confidence: float = 0.5
    evidence: str = ""

    @property
    def reviewed(self) -> bool:
        return self.decision != "skip" and bool(self.reviewer)

    @property
    def text(self) -> str:
        return self.final_text if self.decision == "edit" else self.suggestion


def load_review(path: str | Path) -> tuple[list[ReviewRow], list[str]]:
    rows, errors = [], []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for i, raw in enumerate(csv.DictReader(f), start=2):
            r = {(k or "").strip().lower(): (v or "") for k, v in raw.items()}
            dec_raw = r.get("decision", "").strip().lower()
            dec = DECISIONS.get(dec_raw)
            if dec is None:
                errors.append(f"строка {i}: неизвестное решение «{dec_raw}» (accept/edit/keep/skip)")
                continue
            rows.append(
                ReviewRow(
                    key=r.get("key", "").strip(),
                    decision=dec,
                    final_text=r.get("final_text", ""),
                    suggestion=r.get("suggestion", ""),
                    reviewer=r.get("reviewer", "").strip(),
                    issue_types=[t for t in r.get("issue_types", "").split(",") if t],
                    comment=r.get("comment", "").strip(),
                    severity=sev if (sev := r.get("severity", "").strip()) in SEVERITY_RANK else "medium",
                    confidence=_float(r.get("confidence"), 0.5),
                    evidence=r.get("evidence", "").strip(),
                )
            )
    return rows, errors


def _float(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def merge_review(
    findings: list[Finding], rows: list[ReviewRow], src: dict[str, str], tgt: dict[str, str]
) -> tuple[list[Finding], dict]:
    """Bring the review sheet back into the report:
    - a reviewer's `keep` = "the string is fine, the finding is false" -> the key's findings leave
      the report (they are counted in summary.review.rejected, not silently lost);
    - `accept`/`edit` by a reviewer -> reviewed_by_human = true;
    - rows the reviewer/LLM pass added for keys the tool did not flag become findings themselves,
      reviewed_by_human only if a named reviewer accepted or edited them.
    """
    by_key: dict[str, list[ReviewRow]] = {}
    for r in rows:
        by_key.setdefault(r.key, []).append(r)
    flagged = {f.key for f in findings}
    out, rejected = [], []
    for f in findings:
        rs = by_key.get(f.key, [])
        if any(r.reviewed and r.decision == "keep" for r in rs):
            rejected.append(f.key)
            continue
        f.reviewed_by_human = f.reviewed_by_human or any(r.reviewed for r in rs)
        out.append(f)
    added = 0
    for key, rs in by_key.items():
        if key in flagged:
            continue
        for r in rs:
            if r.decision == "keep" or not r.text:
                continue
            types = [t for t in r.issue_types if t in ISSUE_TYPES] or ["machine_translation_suspect"]
            ev = r.evidence.removeprefix("[добавлено ревью]").strip() or r.comment or "найдено при ревью"
            for t in types:
                out.append(
                    Finding(
                        key, t, r.severity, src.get(key), tgt.get(key), f"найдено при ревью: {ev}",
                        r.text, r.confidence, reviewed_by_human=r.reviewed, rule="review",
                    )
                )  # fmt: skip
                added += 1
    stats = {
        "rejected_by_reviewer": len(set(rejected)),
        "added_by_review": added,
        "rejected_keys": sorted(set(rejected)),
    }
    return out, stats


def reviewed_keys(rows: list[ReviewRow]) -> set[str]:
    return {r.key for r in rows if r.decision != "skip" and r.reviewer}


@dataclass
class ApplyResult:
    arb: ArbFile
    changes: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)  # reviewer said "finding is wrong"
    skipped: int = 0
    text: str = ""  # output file content (format-preserving when the input text is known)


def _validate(new: str, key: str, source: ArbFile | None, escaping: bool) -> str | None:
    nodes, err = icu.try_parse(new, escaping)
    if err:
        return f"{key}: ICU не разбирается: {err}"
    for a in icu.plural_args(nodes):
        if "other" not in a.branches:
            return f"{key}: в {{{a.name}, {a.kind}}} нет ветки other"
    if source is not None:
        src = source.messages().get(key)
        if src is not None:
            s_nodes, s_err = icu.try_parse(src, escaping)
            if not s_err and icu.arg_names(s_nodes) != icu.arg_names(nodes):
                return f"{key}: плейсхолдеры {sorted(icu.arg_names(nodes))} ≠ исходник {sorted(icu.arg_names(s_nodes))}"
    return None


def _set_with_meta(data: dict, key: str, value: str, meta_update: dict) -> dict:
    data[key] = value
    mk = "@" + key
    if isinstance(data.get(mk), dict):
        data[mk].update(meta_update)
        return data
    out: dict = {}
    for k, v in data.items():
        if k == mk:
            continue  # non-dict metadata is replaced
        out[k] = v
        if k == key:
            out[mk] = dict(meta_update)
    return out


def apply_review(
    target: ArbFile,
    rows: list[ReviewRow],
    source: ArbFile | None = None,
    escaping: bool = False,
    include_unreviewed: bool = False,
    suggested_by: str | None = None,
) -> ApplyResult:
    """Apply signed review decisions. With include_unreviewed=True (draft mode) rows without
    a decision but with a suggestion are applied too and marked `x-reviewed: false`."""
    data = json.loads(json.dumps(target.data, ensure_ascii=False))  # deep copy, order kept
    res = ApplyResult(ArbFile(data, target.path, [], target.indent))
    msgs = target.messages()
    seen: set[str] = set()
    values: dict[str, str] = {}
    metas: dict[str, dict] = {}
    for r in rows:
        human = True
        if r.decision == "skip" and include_unreviewed and r.suggestion.strip():
            r = ReviewRow(r.key, "accept", "", r.suggestion, "", r.issue_types, r.comment)
            human = False
        if r.decision == "skip":
            res.skipped += 1
            continue
        if human and not r.reviewer:
            res.errors.append(f"{r.key}: решение «{r.decision}» без имени проверяющего — строка не применена")
            continue
        if r.key in seen:
            res.errors.append(f"{r.key}: повторная строка в ревью — применена первая")
            continue
        seen.add(r.key)
        if r.decision == "keep":
            res.kept.append(r.key)
            continue
        new = r.suggestion if r.decision == "accept" else r.final_text
        if not new.strip():
            res.errors.append(f"{r.key}: решение «{r.decision}», но текст пуст")
            continue
        err = _validate(new, r.key, source, escaping)
        if err:
            res.errors.append(err)
            continue
        old = msgs.get(r.key)
        if old == new:
            res.kept.append(r.key)
            continue
        meta = {"x-reviewed": human, "x-original": old, "x-issues": r.issue_types}
        if human:
            meta["x-reviewed-by"] = r.reviewer
        elif suggested_by:
            meta["x-suggested-by"] = suggested_by
        if r.comment:
            meta["x-review-comment"] = r.comment
        data = _set_with_meta(data, r.key, new, meta)
        values[r.key], metas[r.key] = new, meta
        res.changes.append(
            {
                "key": r.key,
                "old": old,
                "new": new,
                "reviewed_by_human": human,
                "reviewer": r.reviewer if human else None,
                "decision": r.decision,
                "issue_types": r.issue_types,
            }
        )
    res.arb.data = data
    if target.text is not None:
        res.text = target.patched(values, metas)
        res.arb = ArbFile.loads(res.text, target.path)
    else:
        res.text = res.arb.dumps()
    return res
