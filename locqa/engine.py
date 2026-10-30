"""Runs checks, collects file-level diagnostics, merges and filters findings."""
from __future__ import annotations

from .checks import REGISTRY
from .context import Context
from .langs import base_locale, invalid_resource_names, locale_from_filename
from .model import SEVERITY_RANK, Finding, max_severity


def file_diagnostics(ctx: Context) -> None:
    for which, f in (("source", ctx.source), ("target", ctx.target)):
        for k in sorted(set(f.duplicates) - {"@@comment"}):  # repeated section markers are a known convention
            vals = f.occurrences(k)
            distinct = list(dict.fromkeys(str(v) for v in vals))
            if len(distinct) > 1:
                ctx.diag(
                    "warning",
                    "duplicate_key_conflict",
                    f"{which}: ключ «{k}» объявлен {len(vals)} раза с разными значениями; действует последнее: "
                    + " | ".join(distinct),
                    k,
                )
            else:
                ctx.diag("info", "duplicate_key", f"{which}: ключ «{k}» объявлен {len(vals)} раза с одинаковым значением", k)
        for k in f.non_string_keys():
            ctx.diag("error", "non_string_value", f"{which}: значение не строка", k)
        for k, v in f.data.items():
            if k.startswith("@") and not k.startswith("@@"):
                if k[1:] not in f.data:
                    ctx.diag("warning", "orphan_metadata", f"{which}: метаданные «{k}» без ключа «{k[1:]}»", k[1:])
                elif not isinstance(v, dict):
                    ctx.diag("error", "bad_metadata", f"{which}: «{k}» должен быть объектом", k[1:])
        if f.path:
            declared = f.data.get("@@locale")
            by_name = locale_from_filename(f.path)
            if isinstance(declared, str) and by_name and base_locale(declared) != base_locale(by_name):
                ctx.diag("warning", "locale_mismatch", f"{which}: @@locale={declared}, а по имени файла {by_name}")
    for k in invalid_resource_names(ctx.src):
        ctx.diag(
            "warning", "invalid_resource_name",
            "source: имя ключа шаблона недопустимо для gen-l10n (нужен Dart-метод [a-z$][A-Za-z0-9_$]*); "
            "flutter gen-l10n упадёт на шаблоне",
            k,
        )  # fmt: skip
    extra = sorted(set(ctx.tgt) - set(ctx.src))
    for k in extra:
        ctx.diag("warning", "extra_key", "ключ есть в переводе, но отсутствует в исходнике", k)
    if ctx.lang == "und":
        ctx.diag("warning", "unknown_locale", "не удалось определить локаль перевода; языковые проверки отключены")


def merge(findings: list[Finding]) -> list[Finding]:
    """One finding per (key, issue_type): max severity/confidence, evidence concatenated."""
    by: dict[tuple[str, str], Finding] = {}
    for f in findings:
        k = (f.key, f.issue_type)
        if k not in by:
            by[k] = f
            continue
        g = by[k]
        g.severity = max_severity(g.severity, f.severity)
        if f.evidence not in g.evidence:
            g.evidence = f"{g.evidence}; {f.evidence}"
        if f.confidence > g.confidence and f.suggestion:
            g.suggestion = f.suggestion
        elif not g.suggestion:
            g.suggestion = f.suggestion
        g.confidence = max(g.confidence, f.confidence)
        g.rule = ",".join(dict.fromkeys(filter(None, [*g.rule.split(","), *f.rule.split(",")])))
        g.tags = sorted(set(g.tags) | set(f.tags))
        g.reviewed_by_human = g.reviewed_by_human or f.reviewed_by_human
    return sort_findings(list(by.values()))


# When the whole string is wrong (not translated, copied, other language), term/length/style
# findings on the same key are consequences of that, not separate problems: drop them.
WHOLE_STRING = frozenset({"untranslated", "same_as_source", "wrong_language"})
DOMINATED = frozenset(
    {"glossary_violation", "inconsistent_term", "length_overflow", "machine_translation_suspect", "orthography"}
)


def drop_dominated(findings: list[Finding]) -> list[Finding]:
    whole = {f.key for f in findings if f.issue_type in WHOLE_STRING}
    return [f for f in findings if not (f.key in whole and f.issue_type in DOMINATED)]


def sort_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (-SEVERITY_RANK[f.severity], -f.confidence, f.issue_type, f.key))


def run(ctx: Context, checks: list[str] | None = None) -> list[Finding]:
    file_diagnostics(ctx)
    out: list[Finding] = []
    for name, fn in REGISTRY.items():
        if checks is not None and name not in checks:
            continue
        out.extend(fn(ctx))
    return drop_dominated(merge(out))


def split_by_confidence(findings: list[Finding], threshold: float) -> tuple[list[Finding], list[Finding]]:
    kept = [f for f in findings if f.confidence >= threshold]
    dropped = [f for f in findings if f.confidence < threshold]
    return kept, dropped
