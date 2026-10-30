"""Numbers for the methodology section: how much went through a native speaker and how
often the tool was right, measured on reviewed findings."""
from __future__ import annotations

from collections import Counter, defaultdict

from .model import Finding
from .review import ReviewRow


def review_stats(rows: list[ReviewRow], findings: list[Finding] | None = None) -> dict:
    by_decision = Counter(r.decision for r in rows)
    reviewed = [r for r in rows if r.decision != "skip" and r.reviewer]
    per_type: dict[str, Counter] = defaultdict(Counter)
    for r in reviewed:
        for t in r.issue_types or ["?"]:
            per_type[t][r.decision] += 1

    def precision(c: Counter) -> float | None:
        judged = c["accept"] + c["edit"] + c["keep"]
        return round((c["accept"] + c["edit"]) / judged, 3) if judged else None

    signed = Counter(r.decision for r in reviewed)
    out = {
        "review_rows": len(rows),
        "reviewed_by_human": len(reviewed),
        "reviewers": sorted({r.reviewer for r in reviewed}),
        "decisions": dict(by_decision),
        "unsigned_decisions": sum(1 for r in rows if r.decision != "skip" and not r.reviewer),
        "changed_strings": signed["accept"] + signed["edit"],
        "suggestion_accepted_as_is": signed["accept"],
        "rewritten_by_human": signed["edit"],
        "false_positives_kept": signed["keep"],
        # a finding is judged correct when the reviewer changed the string; "keep" = false positive
        "precision_on_reviewed": precision(signed),
        "per_type": {
            t: {**dict(c), "precision": precision(c)} for t, c in sorted(per_type.items())
        },
    }
    if findings is not None:
        keys = {f.key for f in findings}
        out["report_findings"] = len(findings)
        out["report_keys"] = len(keys)
        out["review_coverage_of_report_keys"] = round(len({r.key for r in reviewed} & keys) / len(keys), 3) if keys else None
    return out
