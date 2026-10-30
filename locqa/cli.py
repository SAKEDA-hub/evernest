"""Command-line interface.

  locqa check     --source intl_ru.arb --target intl_kk.arb [--glossary glossary.csv] --out report.json
  locqa validate  intl_kk_fixed.arb [--source intl_ru.arb]
  locqa export-review --report report.json --out review.csv
  locqa apply     --target intl_kk.arb --review review.csv --source intl_ru.arb --out intl_kk_fixed.arb
  locqa mine-terms --source intl_ru.arb --target intl_kk.arb --out terms.csv
  locqa review-stats --review review.csv --report report.json
  locqa seed / eval   self-evaluation on synthetic errors
  locqa translit  --target intl_kk.arb --out intl_kk_Latn.arb
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from . import __version__, bench, engine, icu
from .arb import ArbError, ArbFile
from .checks.terms import mine_terms
from .context import Context, Options
from .glossary import Glossary
from .langs import base_locale
from .model import ISSUE_TYPES, SEVERITY_RANK
from .report import build_report, load_findings, write_html, write_json
from .review import apply_review, export_review, load_review, reviewed_keys


def _err(msg: str) -> None:
    print(f"locqa: {msg}", file=sys.stderr)


def _load_refs(paths: list[str]) -> dict[str, dict[str, str]]:
    refs = {}
    for p in paths or []:
        f = ArbFile.load(p)
        loc = base_locale(f.locale) or Path(p).stem
        refs[loc] = f.messages()
    return refs


def _load_allow(path: str | None) -> set[str]:
    if not path:
        return set()
    words = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            words.update(line.split())
    return words


def _context(a) -> Context:
    source, target = ArbFile.load(a.source), ArbFile.load(a.target)
    tloc = a.target_locale or target.locale
    gcol = base_locale(tloc) or "kk"
    glossary = Glossary.load(a.glossary, target_column=gcol) if getattr(a, "glossary", None) else Glossary()
    refs = _load_refs(getattr(a, "ref", None))
    refs.pop(base_locale(tloc), None)
    opts = Options(
        min_confidence=getattr(a, "min_confidence", 0.5),
        escaping=getattr(a, "icu_escaping", False),
        translit=getattr(a, "translit", False),
        allow_tokens=_load_allow(getattr(a, "allow", None)),
        vocab={w.casefold() for w in _load_allow(getattr(a, "vocab", None))},
    )
    return Context(source, target, glossary, refs, opts, a.source_locale, tloc)


def cmd_check(a) -> int:
    ctx = _context(a)
    findings = engine.run(ctx)
    llm_stats = None
    if a.llm != "off":
        from .llm import LLMClient, estimate, llm_review, select_keys

        keys = select_keys(ctx, findings, a.llm_scope, a.llm_max_items)
        est = estimate(ctx, keys, a.llm_batch)
        if a.llm_dry_run:
            print(json.dumps({"llm_estimate": est}, ensure_ascii=False, indent=2))
            llm_stats = {"enabled": False, "dry_run": est}
        else:
            client = LLMClient.from_env(a.llm, a.llm_cache)
            findings = engine.drop_dominated(engine.merge(findings + llm_review(ctx, client, keys, a.llm_batch)))
            llm_stats = {
                "enabled": True,
                "provider": a.llm,
                "model": client.model,
                "scope": a.llm_scope,
                "keys_sent": len(keys),
                **client.stats,
                "price_usd_per_1m": {"input": client.price_in, "output": client.price_out},
                "cost_usd": round(client.cost_usd, 4),
            }
            if not (client.price_in or client.price_out):
                llm_stats["note"] = "цены не заданы (LOCQA_LLM_PRICE_IN/OUT): cost_usd не рассчитан"

    if a.only:
        wanted = set(a.only.split(","))
        findings = [f for f in findings if f.issue_type in wanted]
    if a.review:
        rows, errors = load_review(a.review)
        for e in errors:
            _err(e)
        done = reviewed_keys(rows)
        for f in findings:
            f.reviewed_by_human = f.reviewed_by_human or f.key in done

    threshold = 0.0 if a.all else a.min_confidence
    kept, dropped = engine.split_by_confidence(findings, threshold)
    report = build_report(ctx, kept, dropped, threshold, llm_stats)
    write_json(report, a.out, flat=a.flat)
    html_path = a.html or str(Path(a.out).with_suffix(".html"))
    if html_path != "-":
        write_html(report, html_path)

    s = report["summary"]
    print(f"{s['findings']} находок в {s['keys_with_findings']} ключах (ниже порога {threshold}: {s['suppressed_below_threshold']})")
    for t, n in s["by_type"].items():
        print(f"  {t:30} {n}")
    errors = [d for d in ctx.diagnostics if d.level == "error"]
    if errors:
        print(f"диагностика: {len(errors)} ошибок файла (см. diagnostics в отчёте)")
    print(f"отчёт: {a.out}" + (f", {html_path}" if html_path != "-" else ""))

    if a.fail_on != "never":
        bar = SEVERITY_RANK[a.fail_on]
        if any(SEVERITY_RANK[f.severity] >= bar for f in kept) or errors:
            return 1
    return 0


def cmd_validate(a) -> int:
    """Pre-flight for `flutter gen-l10n`: JSON, duplicates, ICU syntax, placeholder parity."""
    problems: list[str] = []
    try:
        arb = ArbFile.load(a.file)
    except ArbError as e:
        _err(str(e))
        return 1
    src = ArbFile.load(a.source).messages() if a.source else {}
    dups = sorted(set(arb.duplicates) - {"@@comment"})
    if dups:
        # JSON decoders (incl. Dart's) keep the last value, so this does not break gen-l10n
        print(f"предупреждение: {len(dups)} дублирующихся ключей (действует последнее значение): {', '.join(dups[:10])}…")
    for k in arb.non_string_keys():
        problems.append(f"{k}: значение не строка")
    for k, v in arb.data.items():
        if k.startswith("@") and not k.startswith("@@") and not isinstance(v, dict):
            problems.append(f"{k}: метаданные должны быть объектом")
    for k, v in arb.messages().items():
        nodes, err = icu.try_parse(v, a.icu_escaping)
        if err:
            problems.append(f"{k}: {err}")
            continue
        for p in icu.plural_args(nodes):
            if "other" not in p.branches:
                problems.append(f"{k}: {{{p.name}, {p.kind}}} без ветки other")
        if k in src:
            s_nodes, s_err = icu.try_parse(src[k], a.icu_escaping)
            if not s_err and icu.arg_names(s_nodes) != icu.arg_names(nodes):
                problems.append(f"{k}: плейсхолдеры {sorted(icu.arg_names(nodes))} ≠ {sorted(icu.arg_names(s_nodes))}")
    for p in problems:
        print(p)
    print(f"{a.file}: {'OK' if not problems else f'{len(problems)} проблем'}")
    return 1 if problems else 0


def cmd_export_review(a) -> int:
    findings = load_findings(a.report)
    if a.min_confidence:
        findings = [f for f in findings if f.confidence >= a.min_confidence]
    extra = json.loads(Path(a.suggestions).read_text(encoding="utf-8")) if a.suggestions else None
    n = export_review(findings, a.out, extra)
    print(f"{n} строк на ревью → {a.out}")
    return 0


def cmd_apply(a) -> int:
    target = ArbFile.load(a.target)
    source = ArbFile.load(a.source) if a.source else None
    rows, errors = load_review(a.review)
    res = apply_review(target, rows, source, a.icu_escaping, a.include_unreviewed, a.suggested_by)
    Path(a.out).write_text(res.text, encoding="utf-8")
    sidecar = a.sidecar or str(Path(a.out).with_suffix(".review.json"))
    Path(sidecar).write_text(
        json.dumps(
            {"changes": res.changes, "kept_as_is_after_review": res.kept, "errors": errors + res.errors},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    for e in errors + res.errors:
        _err(e)
    unreviewed = sum(1 for c in res.changes if not c["reviewed_by_human"])
    print(
        f"изменено {len(res.changes)} строк (проверено человеком: {len(res.changes) - unreviewed}, "
        f"черновик x-reviewed=false: {unreviewed}), "
        f"оставлено как есть после ревью {len(res.kept)}, без решения {res.skipped}, ошибок {len(errors) + len(res.errors)}"
    )
    print(f"→ {a.out}, {sidecar}")
    return 1 if (errors or res.errors) and a.strict else 0


def cmd_mine_terms(a) -> int:
    a.glossary = None
    ctx = _context(a)
    cands = mine_terms(ctx, min_support=a.min_support, min_coverage=a.min_coverage)
    with open(a.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ru", "kk", "en", "note", "support", "coverage", "outliers", "status"])
        for c in cands:
            w.writerow(
                [c.ru_example, c.tgt_example, "", "", c.support, f"{c.coverage:.2f}", " ".join(c.outliers[:10]), "candidate"]
            )
    print(f"{len(cands)} кандидатов в термины → {a.out} (проверить носителем перед включением в glossary.csv)")
    return 0


def cmd_review_stats(a) -> int:
    from .stats import review_stats

    rows, errors = load_review(a.review)
    for e in errors:
        _err(e)
    res = review_stats(rows, load_findings(a.report) if a.report else None)
    text = json.dumps(res, ensure_ascii=False, indent=2)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def cmd_seed(a) -> int:
    source, target = ArbFile.load(a.source), ArbFile.load(a.target)
    glossary = Glossary.load(a.glossary) if a.glossary else Glossary()
    seeded, answers = bench.seed(source, target, a.n, glossary, _load_refs(a.ref), a.seed)
    seeded.save(a.out)
    bench.save_answers(answers, a.answers)
    print(f"подложено {len(answers)} ошибок → {a.out}, ответы → {a.answers}")
    return 0


def cmd_eval(a) -> int:
    res = bench.evaluate(load_findings(a.report), bench.load_answers(a.answers))
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_translit(a) -> int:
    from . import translit

    if a.table:
        translit.load_table(a.table)
    out, errors = translit.translit_arb(ArbFile.load(a.target), a.icu_escaping)
    out.save(a.out)
    for e in errors:
        _err(e)
    print(f"→ {a.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="locqa", description="QA локализации Flutter ARB")
    p.add_argument("--version", action="version", version=f"locqa {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, target=True):
        sp.add_argument("--source", required=True, help="исходный ARB (template), например intl_ru.arb")
        if target:
            sp.add_argument("--target", required=True, help="проверяемый ARB")
        sp.add_argument("--source-locale")
        sp.add_argument("--target-locale")
        sp.add_argument("--icu-escaping", action="store_true", help="как use-escaping: true в l10n.yaml")

    c = sub.add_parser("check", help="найти проблемы и записать отчёт")
    common(c)
    c.add_argument("--glossary")
    c.add_argument("--out", required=True)
    c.add_argument("--html", help="путь HTML-отчёта (по умолчанию рядом с --out; «-» — не писать)")
    c.add_argument("--ref", action="append", default=[], help="ARB других языков для wrong_language (повторяемый)")
    c.add_argument("--allow", help="файл слов, совпадение которых с исходником допустимо")
    c.add_argument("--vocab", help="словарь целевого языка (по слову/строке) для проверки орфографии")
    c.add_argument("--min-confidence", type=float, default=0.5)
    c.add_argument("--all", action="store_true", help="не отсекать находки по confidence")
    c.add_argument("--only", help=f"оставить только типы: {','.join(ISSUE_TYPES)}")
    c.add_argument("--flat", action="store_true", help="report.json как голый массив находок")
    c.add_argument("--translit", action="store_true", help="учитывать длину латинской версии (kk)")
    c.add_argument("--review", help="review.csv: отметить reviewed_by_human у проверенных ключей")
    c.add_argument("--fail-on", choices=["high", "medium", "low", "never"], default="never")
    c.add_argument("--llm", choices=["off", "anthropic", "openai"], default="off")
    c.add_argument("--llm-scope", choices=["flagged", "all"], default="flagged")
    c.add_argument("--llm-max-items", type=int)
    c.add_argument("--llm-batch", type=int, default=20)
    c.add_argument("--llm-cache", default=".locqa_llm_cache.json")
    c.add_argument("--llm-dry-run", action="store_true", help="только оценка объёма токенов")
    c.set_defaults(fn=cmd_check)

    v = sub.add_parser("validate", help="проверка, которую должен пройти файл перед flutter gen-l10n")
    v.add_argument("file")
    v.add_argument("--source")
    v.add_argument("--icu-escaping", action="store_true")
    v.set_defaults(fn=cmd_validate)

    e = sub.add_parser("export-review", help="таблица для носителя языка")
    e.add_argument("--report", required=True)
    e.add_argument("--out", required=True)
    e.add_argument("--min-confidence", type=float, default=0.0)
    e.add_argument("--suggestions", help='JSON {key: {"suggestion": ..., "comment": ...}} (LLM/коллега)')
    e.set_defaults(fn=cmd_export_review)

    ap = sub.add_parser("apply", help="применить решения ревью → исправленный ARB")
    ap.add_argument("--target", required=True)
    ap.add_argument("--review", required=True)
    ap.add_argument("--source", help="для проверки плейсхолдеров (рекомендуется)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--sidecar")
    ap.add_argument("--icu-escaping", action="store_true")
    ap.add_argument("--strict", action="store_true", help="код 1, если были отклонённые строки")
    ap.add_argument(
        "--include-unreviewed",
        action="store_true",
        help="черновик: применить и предложения без решения, пометив их x-reviewed=false",
    )
    ap.add_argument("--suggested-by", help="кто предложил непроверенные правки (в x-suggested-by), напр. «LLM»")
    ap.set_defaults(fn=cmd_apply)

    m = sub.add_parser("mine-terms", help="кандидаты в глоссарий из пар строк")
    common(m)
    m.add_argument("--out", required=True)
    m.add_argument("--min-support", type=int, default=4)
    m.add_argument("--min-coverage", type=float, default=0.75)
    m.set_defaults(fn=cmd_mine_terms)

    rs = sub.add_parser("review-stats", help="цифры для методологии: сколько проверено, precision по типам")
    rs.add_argument("--review", required=True)
    rs.add_argument("--report")
    rs.add_argument("--out")
    rs.set_defaults(fn=cmd_review_stats)

    s = sub.add_parser("seed", help="подложить синтетические ошибки для самопроверки")
    s.add_argument("--source", required=True)
    s.add_argument("--target", required=True)
    s.add_argument("--glossary")
    s.add_argument("--ref", action="append", default=[])
    s.add_argument("--n", type=int, default=100)
    s.add_argument("--seed", type=int, default=13)
    s.add_argument("--out", required=True)
    s.add_argument("--answers", required=True)
    s.set_defaults(fn=cmd_seed)

    ev = sub.add_parser("eval", help="recall отчёта по подложенным ошибкам")
    ev.add_argument("--report", required=True)
    ev.add_argument("--answers", required=True)
    ev.set_defaults(fn=cmd_eval)

    t = sub.add_parser("translit", help="казахская кириллица → латиница (ICU-безопасно)")
    t.add_argument("--target", required=True)
    t.add_argument("--out", required=True)
    t.add_argument("--table", help="JSON-таблица замен вместо встроенной")
    t.add_argument("--icu-escaping", action="store_true")
    t.set_defaults(fn=cmd_translit)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except (ArbError, OSError, RuntimeError, ValueError, KeyError) as e:
        _err(str(e))
        return 2
