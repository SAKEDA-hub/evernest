"""untranslated, same_as_source, placeholder_mismatch, plural_mismatch."""
from __future__ import annotations

import re

from .. import icu
from ..context import Context
from ..model import Finding, max_severity
from ..text import norm, tokens, word_script
from .lang_checks import RU_FUNC, RU_MORPH

VALID_PLURAL = {"zero", "one", "two", "few", "many", "other"}
PRINTF = re.compile(r"%(?:\d+\$)?[sdif@]")


def _names(text: str, ctx: Context) -> set[str]:
    nodes, _ = icu.try_parse(text, ctx.options.escaping)
    return icu.arg_names(nodes) if nodes is not None else set()
_CYR_TO_LAT = str.maketrans("аеорсхукмтвАЕОРСХУКМТВН", "aeopcxykmtbAEOPCXYKMTBH")


def _fix_placeholder_homoglyphs(tgt: str, expected: set[str], escaping: bool) -> str | None:
    """{nаme} with a Cyrillic 'а' -> {name}, if that makes the placeholder set match."""
    fixed = re.sub(r"\{\s*([^{},\s]+)", lambda m: m.group(0).translate(_CYR_TO_LAT), tgt)
    if fixed == tgt:
        return None
    nodes, err = icu.try_parse(fixed, escaping)
    if err or icu.arg_names(nodes) != expected:
        return None
    return fixed


def check_untranslated(ctx: Context):
    for key, src in ctx.src.items():
        if not src.strip():
            continue
        tgt = ctx.tgt.get(key)
        sev, conf = "high", 0.99
        if tgt is None:
            ev = "значение не является строкой" if key in ctx.target.data else "ключ отсутствует в целевом файле"
        elif not tgt.strip():
            ev = "пустое значение"
            if _is_fragment(key, src):
                # prefix/suffix pieces glued in code: word order may legitimately make them empty
                sev, conf = "medium", 0.6
                ev += "; исходник — фрагмент фразы, пустой перевод может быть намеренным из-за порядка слов"
        else:
            continue
        yield Finding(key, "untranslated", sev, src, tgt, ev, ctx.glossary.exact(src), conf, rule="untranslated")


FRAGMENT_KEY = re.compile(r"(prefix|suffix|part\d*|postfix|before|after)$|(prefix|suffix|part)\d*_|Title\d+$", re.IGNORECASE)


def _is_fragment(key: str, src: str) -> bool:
    return bool(FRAGMENT_KEY.search(key)) or src != src.strip() or len(tokens(src)) <= 1


def check_same_as_source(ctx: Context):
    if ctx.same_language:
        return
    for key, src, tgt in ctx.pairs():
        if norm(src) != norm(tgt):
            continue
        words = tokens(ctx.plain("src", key))
        cyr = [w for w in words if word_script(w) == "cyr"]
        if not cyr:
            continue  # digits/punctuation/placeholders only, or Latin brand/tech term
        remaining = [w for w in cyr if w.casefold() not in ctx.keep_tokens]
        if not remaining:
            continue  # brand, glossary keep-as-is term or known loanword
        if all(len(w) <= 3 for w in cyr):
            continue  # units and abbreviations: кг, л, ур., Д.
        if len(cyr) == 1:
            w = remaining[0].casefold()
            if RU_MORPH.search(w) or w in RU_FUNC:
                sev, conf = "medium", 0.75
                ev = f"однословная строка совпадает с исходной; «{remaining[0]}» — русская словоформа"
            else:
                # most single identical nouns are loans (Энергия, Фестиваль): keep below the default threshold
                sev, conf = "low", 0.45
                ev = (
                    f"однословная строка совпадает с исходной («{remaining[0]}»); "
                    "вероятно заимствование, проверить носителем"
                )
        elif len(cyr) <= 3 and all(w[:1].isupper() for w in cyr) and not any(
            RU_MORPH.search(w.casefold()) or w.casefold() in RU_FUNC for w in cyr
        ):
            sev, conf = "medium", 0.55
            ev = "короткое название с заглавных букв совпадает с исходным: возможно имя собственное, проверить"
        else:
            sev, conf = "high", 0.9
            ev = f"строка из {len(words)} слов дословно совпадает с исходной; не похожа на бренд или число"
        yield Finding(key, "same_as_source", sev, src, tgt, ev, ctx.glossary.exact(src), conf, rule="same_as_source")


def _plural_problems(arg: icu.Arg, cats: frozenset | None, locale: str) -> tuple[list[str], str]:
    probs: list[str] = []
    sev = "low"
    sels = set(arg.branches)
    if "other" not in sels:
        probs.append(f"{{{arg.name}, {arg.kind}}}: нет обязательной ветки other")
        sev = "high"
    if arg.kind == "select":
        return probs, sev
    bad = sorted(s for s in sels if s not in VALID_PLURAL and not re.fullmatch(r"=\d+", s))
    if bad:
        probs.append(f"{{{arg.name}, {arg.kind}}}: недопустимые селекторы {', '.join(bad)}")
        sev = "high"
    if cats and arg.kind == "plural":
        missing = sorted(c for c in cats if c != "other" and c not in sels and not (c == "one" and "=1" in sels))
        unused = sorted(s for s in sels if s in VALID_PLURAL and s != "other" and s not in cats)
        if missing:
            probs.append(f"{{{arg.name}}}: нет форм {', '.join(missing)}, нужных для {locale}")
            sev = max_severity(sev, "medium")
        if unused:
            probs.append(f"{{{arg.name}}}: формы {', '.join(unused)} не используются в {locale}")
    return probs, sev


def check_icu(ctx: Context):
    """placeholder_mismatch + plural_mismatch (plural/select structure)."""
    for key, src, tgt in ctx.pairs():
        s_nodes, s_err = ctx.parse("src", key)
        if s_err:
            ctx.diag("warning", "source_icu_error", f"исходная строка не разбирается как ICU: {s_err}", key)
            continue
        t_nodes, t_err = ctx.parse("tgt", key)
        s_plurals = {a.name: a for a in icu.plural_args(s_nodes)}
        if t_err:
            looks_plural = bool(s_plurals) or re.search(r"\{\s*\w+\s*,\s*(plural|selectordinal|select)\b", tgt)
            sugg = _fix_placeholder_homoglyphs(tgt, icu.arg_names(s_nodes), ctx.options.escaping)
            ev = f"ICU-синтаксис перевода не разбирается: {t_err}; flutter gen-l10n упадёт"
            if sugg:
                ev += "; в имени плейсхолдера кириллические буквы вместо латинских"
            yield Finding(
                key,
                "plural_mismatch" if looks_plural else "placeholder_mismatch",
                "high",
                src,
                tgt,
                ev,
                sugg,
                0.98,
                rule="icu_syntax",
            )
            continue

        s_names, t_names = icu.arg_names(s_nodes), icu.arg_names(t_nodes)
        missing, extra = s_names - t_names, t_names - s_names
        s_pf, t_pf = sorted(PRINTF.findall(src)), sorted(PRINTF.findall(tgt))
        if s_pf != t_pf and not (missing or extra):
            yield Finding(
                key, "placeholder_mismatch", "high", src, tgt,
                f"printf-плейсхолдеры не совпадают: в исходнике {s_pf or 'нет'}, в переводе {t_pf or 'нет'}",
                None, 0.95, rule="printf_set",
            )  # fmt: skip
        if missing or extra:
            parts = []
            if missing:
                parts.append("нет " + ", ".join(f"{{{n}}}" for n in sorted(missing)))
            if extra:
                parts.append("лишние " + ", ".join(f"{{{n}}}" for n in sorted(extra)))
            sugg = None
            if len(missing) == 1 and len(extra) == 1:
                (m,), (x,) = missing, extra
                sugg = re.sub(r"\{\s*" + re.escape(x) + r"\s*([,}])", "{" + m + r"\1", tgt)
            elif missing and not extra:
                # braces lost: "Жаңарады: time" -> "Жаңарады: {time}"
                fixed = tgt
                for m in missing:
                    fixed = re.sub(r"(?<![\w{])" + re.escape(m) + r"(?![\w}])", "{" + m + "}", fixed, count=1)
                if icu.try_parse(fixed, ctx.options.escaping)[0] is not None and _names(fixed, ctx) == s_names:
                    sugg = fixed
                    parts.append("имя плейсхолдера осталось в тексте без фигурных скобок")
            # a dropped placeholder gets no suggestion: a human decides where it goes
            yield Finding(
                key,
                "placeholder_mismatch",
                "high",
                src,
                tgt,
                "плейсхолдеры не совпадают с исходником: " + "; ".join(parts),
                sugg,
                0.97,
                rule="placeholder_set",
            )

        t_plurals = {a.name: a for a in icu.plural_args(t_nodes)}
        problems: list[str] = []
        sev = "low"
        for name, sa in s_plurals.items():
            ta = t_plurals.get(name)
            if ta is None and name in t_names:
                problems.append(f"в исходнике {{{name}, {sa.kind}}}, в переводе — простой плейсхолдер")
                sev = max_severity(sev, "medium")
            elif ta is not None and ta.kind != sa.kind:
                problems.append(f"{{{name}}}: тип {sa.kind} в исходнике, {ta.kind} в переводе")
                sev = max_severity(sev, "high")
        for ta in t_plurals.values():
            p, s = _plural_problems(ta, ctx.profile.plural_categories, ctx.target_locale)
            problems += p
            if p:
                sev = max_severity(sev, s)
        if problems:
            conf = 0.95 if sev == "high" else 0.8 if sev == "medium" else 0.6
            yield Finding(key, "plural_mismatch", sev, src, tgt, "; ".join(problems), None, conf, rule="plural_forms")
