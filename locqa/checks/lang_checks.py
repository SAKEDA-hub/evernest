"""Language-aware checks: suffix_harmony, orthography, wrong_language, machine_translation_suspect.

Kazakh rules are the most developed; Uzbek gets script/apostrophe/language checks;
other locales get the language-independent parts (mixed scripts, typography, refs).
"""
from __future__ import annotations

import re
from itertools import combinations

from ..context import Context
from ..model import Finding
from ..text import (
    KK_SPECIFIC,
    UZ_CYR_SPECIFIC,
    dominant_script,
    has_kk_letters,
    norm,
    replace_word,
    tokens,
    word_script,
)

# --- suffix_harmony --------------------------------------------------------------------

_CASES = {
    "dative": ("ға", "ге", "қа", "ке", "на", "не"),
    "genitive": ("ның", "нің", "дың", "дің", "тың", "тің"),
    "accusative": ("ны", "ні", "ды", "ді", "ты", "ті"),
    "locative": ("нда", "нде", "да", "де", "та", "те"),
    "ablative": ("дан", "ден", "тан", "тен", "нан", "нен"),
    "instrumental": ("мен", "бен", "пен"),
    "plural": ("лар", "лер", "дар", "дер", "тар", "тер"),
    "ordinal": ("інші", "ыншы", "нші", "ншы", "ші", "шы"),
}
_SUFFIXES = sorted(((s, c) for c, ss in _CASES.items() for s in ss), key=lambda x: -len(x[0]))
CASE_RU = {
    "dative": "дательный (-ға/-ге/-қа/-ке/-на/-не)",
    "genitive": "родительный (-ның/-нің/-дың/-дің/-тың/-тің)",
    "accusative": "винительный (-ны/-ні/-ды/-ді/-ты/-ті)",
    "locative": "местный (-да/-де/-та/-те/-нда/-нде)",
    "ablative": "исходный (-дан/-ден/-тан/-тен/-нан/-нен)",
    "instrumental": "творительный (-мен/-бен/-пен)",
    "plural": "множественное число (-лар/-лер/-дар/-дер/-тар/-тер)",
    "ordinal": "порядковое (-ші/-шы/-інші/-ыншы)",
}
# Buffer noun with fixed (back-vowel) harmony: "{name} ойыншысына" = "игроку {name}".
PLAYER_BUFFER = {
    "dative": "ойыншысына",
    "genitive": "ойыншысының",
    "accusative": "ойыншысын",
    "locative": "ойыншысында",
    "ablative": "ойыншысынан",
    "instrumental": "ойыншысымен",
}
PERSON_HINT = re.compile(
    r"name|nick|player|user|sender|receiver|recipient|target|owner|member|leader|admin|killer|victim|"
    r"buyer|seller|friend|partner|author|inviter|invitee",
    re.IGNORECASE,
)
NUMERIC_HINT = re.compile(
    r"count|amount|num|qty|quantity|sum|price|cost|level|lvl|value|money|coins|days|hours|minutes|"
    r"seconds|percent|^n$|^x$",
    re.IGNORECASE,
)
SUFFIX_RE = re.compile(r"(\{\s*([A-Za-z_]\w*)\s*\}|#)([-‐‑'’ʼ]?)([^\W\d_]+)")


def classify_suffix(letters: str) -> tuple[str | None, str | None]:
    low = letters.casefold()
    for suf, case in _SUFFIXES:
        if low.startswith(suf):
            return case, suf
    return None, None


def _is_case_suffix(letters: str) -> bool:
    case, suf = classify_suffix(letters)
    # the attached letters must be the ending itself (+ at most a short possessive/case tail),
    # not a whole word that merely starts like one
    return case is not None and len(letters) - len(suf) <= 3


def suggest_rephrase(target: str, matches: list[re.Match]) -> str | None:
    """Auto-rephrase: '{name}-ға' -> '{name} ойыншысына' for person placeholders.
    Returns None when any attached suffix cannot be rewritten safely."""
    out = target
    for m in sorted(matches, key=lambda m: m.start(), reverse=True):
        name, letters = m.group(2), m.group(4)
        case, suf = classify_suffix(letters)
        if not name or not case or suf != letters.casefold() or case not in PLAYER_BUFFER:
            return None
        if not PERSON_HINT.search(name):
            return None
        head = out[: m.start()]
        cut = re.search(r"(?i)ойыншы(ға|ның|ны|да|дан|мен)?\s*$", head)
        if cut:
            head = head[: cut.start()]
        repl = "{" + name + "} " + PLAYER_BUFFER[case]
        out = head + repl + out[m.end() :]
    return out


def check_suffix_harmony(ctx: Context):
    if ctx.lang != "kk":
        return
    for key, src, tgt in ctx.pairs():
        nodes, err = ctx.parse("tgt", key)
        if err:
            continue
        has_plural = "plural" in tgt or "selectordinal" in tgt
        # only case/plural/ordinal endings: "{place}-орын", "{n}-күн" are the standard
        # "number-hyphen-noun" spelling and are fine
        matches = [
            m
            for m in SUFFIX_RE.finditer(tgt)
            if word_script(m.group(4)) == "cyr"
            and (m.group(1) != "#" or has_plural)
            and _is_case_suffix(m.group(4))
        ]
        if not matches:
            continue
        parts, tags = [], []
        for m in matches:
            ph = m.group(1)
            case, _ = classify_suffix(m.group(4))
            what = CASE_RU.get(case, "аффикс")
            parts.append(f"«{ph}{m.group(3)}{m.group(4)}»: {what}")
            name = m.group(2) or ""
            if ph == "#" or NUMERIC_HINT.search(name):
                tags.append("needs_code_handling")
        sugg = suggest_rephrase(tgt, matches)
        if sugg is None:
            tags.append("needs_code_handling")
        ev = (
            "аффикс жёстко прикреплён к подставляемому значению: "
            + "; ".join(parts)
            + ". Вариант окончания зависит от последнего звука значения, известного только в рантайме"
        )
        if sugg:
            ev += ". Предложена переформулировка через «ойыншы» (гармония фиксирована)"
        else:
            ev += ". Нужна переформулировка носителем или обработка в коде"
        conf = 0.95 if any(m.group(3) for m in matches) else 0.9
        yield Finding(key, "suffix_harmony", "high", src, tgt, ev, sugg, conf, rule="suffix_after_placeholder", tags=tags)


# --- orthography -----------------------------------------------------------------------

LAT_TO_CYR = {
    "a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "x": "х", "y": "у", "k": "к", "m": "м",
    "t": "т", "h": "һ", "i": "і", "b": "в",
    "A": "А", "E": "Е", "O": "О", "P": "Р", "C": "С", "X": "Х", "Y": "У", "K": "К", "M": "М",
    "T": "Т", "H": "Н", "I": "І", "B": "В",
}  # fmt: skip
CYR_TO_LAT = {v: k for k, v in LAT_TO_CYR.items() if v not in "һіІ"}
KK_SUBST = {"а": "ә", "г": "ғ", "к": "қ", "н": "ң", "о": "ө", "у": "ұү", "и": "і", "х": "һ"}
UZ_OKINA = "ʻ"  # U+02BB, official for oʻ gʻ


def _suffix_n_swap(word: str, variant: str) -> bool:
    """н→ң near the end is a grammatical alternation (-ын 3rd person vs -ың 2nd person:
    «жылын»/«жылың»), not a spelling error."""
    return any(a == "н" and b == "ң" and i >= len(word) - 4 for i, (a, b) in enumerate(zip(word, variant)))


def kk_letter_variants(word: str, max_changes: int = 2) -> set[str]:
    w = word.casefold()
    pos = [i for i, c in enumerate(w) if c in KK_SUBST][:10]
    out: set[str] = set()
    for i in pos:
        for r in KK_SUBST[w[i]]:
            out.add(w[:i] + r + w[i + 1 :])
    if max_changes >= 2:
        for i, j in combinations(pos, 2):
            for r1 in KK_SUBST[w[i]]:
                for r2 in KK_SUBST[w[j]]:
                    out.add(w[:i] + r1 + w[i + 1 : j] + r2 + w[j + 1 :])
    return out


def _fix_mixed(word: str, to_script: str, lang: str) -> str:
    if to_script == "cyr":
        table = dict(LAT_TO_CYR)
        if lang != "kk":
            table.pop("h"), table.pop("i"), table.pop("I")
        return "".join(table.get(c, c) for c in word)
    return "".join(CYR_TO_LAT.get(c, c) for c in word)


def check_orthography(ctx: Context):
    script = ctx.target_script
    okina_share = _okina_share(ctx) if ctx.lang == "uz" else None
    for key, src, tgt in ctx.pairs():
        text = ctx.plain("tgt", key, " ")
        problems: list[tuple[str, float, str]] = []  # (evidence, confidence, severity)
        sugg = tgt

        # 1. mixed Cyrillic/Latin inside one word (homoglyphs)
        # "Telegram-ды", "PvP-де": Latin name + Cyrillic suffix after a hyphen is normal
        parts = {p for w in tokens(text) for p in re.split(r"[-'’ʼ`]", w) if p}
        for w in parts:
            if word_script(w) == "mixed" and script in ("cyr", "lat"):
                fixed = _fix_mixed(w, script, ctx.lang)
                if word_script(fixed) == script:
                    problems.append((f"в слове «{w}» смешаны кириллица и латиница", 0.95, "medium"))
                    sugg = sugg.replace(w, fixed)
                else:
                    problems.append((f"в слове «{w}» смешаны кириллица и латиница", 0.85, "medium"))

        # 2. Kazakh letters typed as Russian look-alikes (corpus-frequency evidence)
        if ctx.lang == "kk":
            for w in set(tokens(text)):
                lw = w.casefold()
                if word_script(w) != "cyr" or len(lw) < 3 or lw in ctx.keep_tokens or lw in ctx.source_tokens:
                    continue
                f_w = ctx.word_evidence(lw)  # exact: typos are rare
                if f_w > 2:
                    continue

                def support(v: str) -> int:
                    # Inflection family: words sharing the fixed spelling up to two letters past
                    # the change ("енгізі…" supports «енгизіңіз»). Shorter prefixes would count
                    # unrelated words that merely share a beginning.
                    first = next(i for i, (a, b) in enumerate(zip(lw, v)) if a != b)
                    pre = max(first + 3, 5)
                    if pre < len(v):
                        return ctx.word_evidence(v[:pre], family=True, prefix_len=pre)
                    return ctx.word_evidence(v)

                cands = [v for v in kk_letter_variants(lw) if not _suffix_n_swap(lw, v)]
                # ties (same stem family) go to the exactly attested, minimal change
                best = max(
                    cands,
                    key=lambda v: (support(v), ctx.word_evidence(v), -sum(a != b for a, b in zip(lw, v))),
                    default=None,
                )
                f_b = support(best) if best else 0
                need = 10 * f_w if f_w >= 2 else 3 * f_w  # an attested word needs overwhelming evidence
                if best:
                    first = next(i for i, (a, b) in enumerate(zip(lw, best)) if a != b)
                    pre = max(first + 3, 5)
                    # the suspect's own stem family is attested too («жанармай» vs «жаңарту»):
                    # two real words, not a typo
                    if pre < len(lw) and ctx.word_evidence(lw[:pre], family=True, prefix_len=pre) > 2:
                        best = None
                if best and f_b >= 3 and f_b >= need:
                    diff = ", ".join(f"«{a}»→«{b}»" for a, b in zip(lw, best) if a != b)
                    problems.append(
                        (
                            f"«{w}» встречается {f_w}×, а «{best}» — {f_b}× (файл + словарь); вероятна русская буква вместо казахской ({diff})",
                            0.8 if f_b >= 10 else 0.65,
                            "medium",
                        )
                    )
                    sugg = replace_word(sugg, w, best)

        # 3. Uzbek oʻ/gʻ apostrophe consistency (only when the file has a clear convention)
        if okina_share is not None and okina_share >= 0.8:
            bad = re.findall(r"(?i)\b\w*[og]['’`ʼ]\w*", text)
            if bad:
                problems.append(
                    (
                        f"апостроф в «{bad[0]}» не U+02BB ʻ, хотя файл в {okina_share:.0%} случаев использует ʻ",
                        0.7,
                        "low",
                    )
                )
                sugg = re.sub(r"(?i)([og])['’`ʼ]", lambda m: m.group(1) + UZ_OKINA, sugg)

        # 4. typography introduced by translation
        if "  " in tgt.strip() and "  " not in src:
            problems.append(("двойной пробел", 0.9, "low"))
            sugg = re.sub(r"(?<=\S) {2,}(?=\S)", " ", sugg)
        if re.search(r"\S\s+[,.!?:;](?!\.)", tgt) and not re.search(r"\S\s+[,.!?:;]", src):
            problems.append(("пробел перед знаком препинания", 0.85, "low"))
            sugg = re.sub(r"(?<=\S)\s+([,.!?:;])(?!\.)", r"\1", sugg)
        rep = re.search(r"(?i)(?<!\w)(\w{2,})\s+\1(?!\w)", text)
        if rep and not re.search(r"(?i)(?<!\w)(\w{2,})\s+\1(?!\w)", ctx.plain("src", key, " ")):
            problems.append((f"повтор слова «{rep.group(1)}»", 0.6, "medium"))

        if not problems:
            continue
        sev = "medium" if any(p[2] == "medium" for p in problems) else "low"
        conf = max(p[1] for p in problems)
        yield Finding(
            key,
            "orthography",
            sev,
            src,
            tgt,
            "; ".join(p[0] for p in problems),
            sugg if sugg != tgt else None,
            conf,
            rule="orthography",
        )


def _okina_share(ctx: Context) -> float | None:
    good = bad = 0
    for v in ctx.tgt.values():
        good += len(re.findall(r"(?i)[og]ʻ", v))
        bad += len(re.findall(r"(?i)[og]['’`ʼ](?=\w)", v))
    total = good + bad
    return good / total if total >= 20 else None


# --- wrong_language / machine_translation_suspect --------------------------------------

RU_FUNC = frozenset(
    "и в во на для что это с со по от из к ко об за как но или вы ваш ваша ваше ваши вас вам "
    "мы она они его её ее их при без уже ещё еще если чтобы только нет все всё этот эта эти "
    "был была было будет можно нельзя".split()
)  # excludes Kazakh homographs: да не он у о мен
RU_MORPH = re.compile(
    r"(ться|тся|ость|ости|ение|ения|ению|анием|ание|ания|анию|ный|ная|ное|ные|ного|ному|ными|"
    r"ешь|ают|яют|ировать|ировал|ованный|ено|ано|ский|цкий|ские|ская|ское|ского)$"
)
UZ_LAT_WORDS = frozenset(
    "va uchun bilan emas ham yoki qilish kerak mumkin yangi barcha hech tasdiqlash bekor "
    "o'yinchi oʻyinchi o‘yinchi pul sotib olish".split()
)
UZ_CYR_WORDS = frozenset("ва учун билан эмас ёки керак мумкин янги барча ҳеч".split())
EN_WORDS = frozenset("the to you your and of is are for with not this please be can will".split())
ES_PT_WORDS = frozenset(
    "para por favor de del la el los las que una um uma não nao é o os no na se su seu sua com con "
    "ser será sera aplicativo perfil juego jogo".split()
)
UZ_APOS = re.compile(r"(?i)\b\w*[og][ʻ'’‘`]\w*")
CALQUES = [
    (re.compile(r"(?i)болып табылады"), "«болып табылады» — книжная калька «является» в UI"),
    (re.compile(r"(?i)жүзеге асыр"), "«жүзеге асыру» — калька «осуществить»"),
]


URL = re.compile(r"^\s*(https?://|www\.)\S+\s*$", re.IGNORECASE)


def _ref_hit(ctx: Context, key: str, tgt: str, src: str) -> str | None:
    if URL.match(tgt) or not any(c.isalpha() for c in tgt) or norm(tgt) == norm(src):
        return None
    hits = [loc for loc, msgs in ctx.refs.items() if msgs.get(key) and norm(msgs[key]) == norm(tgt)]
    if not hits:
        return None
    if ctx.looks_native(ctx.plain("tgt", key, " ")):
        return None  # shared brand + native words ("MIDAS CREW ЖИНАҒЫ")
    if len(hits) >= 2 and len(ctx.refs) >= 2:
        # identical in every reference language: an untranslatable name ("SUMMER RUSH"), not a language slip
        return None
    if ctx.target_script == dominant_script(tgt):
        # same alphabet as the reference (uz vs en): names, units, "OK" coincide legitimately;
        # require an actual sentence in the other language
        lw = [w.casefold() for w in tokens(tgt)]
        if hits[0] == "en" and not any(w in EN_WORDS for w in lw):
            return None
        if hits[0] != "en" and len(lw) < 3:
            return None
    return hits[0]


def _wrong_language(ctx: Context, key: str, src: str, tgt: str) -> tuple[str, float, str] | None:
    text = ctx.plain("tgt", key, " ")
    words = tokens(text)
    if not words:
        return None
    lw = [w.casefold() for w in words]
    cyr = sum(1 for w in words if word_script(w) == "cyr")
    lat = sum(1 for w in words if word_script(w) == "lat")
    src_script = dominant_script(ctx.plain("src", key, " "))
    exp = ctx.target_script

    if ctx.lang == "kk":
        if any(c in UZ_CYR_SPECIFIC for c in text):
            return "uz", 0.9, "буквы ў/ҳ отсутствуют в казахском алфавите — похоже на узбекскую кириллицу"
        if lat >= 2 and lat > cyr and src_script == "cyr":
            if UZ_APOS.search(text) or any(w in UZ_LAT_WORDS for w in lw):
                return "uz", 0.85, "латиница с узбекскими маркерами (oʻ/gʻ, служебные слова)"
            if ctx.looks_native(text):
                return None  # Latin brand inside Kazakh text
            if any(w in EN_WORDS for w in lw):
                return "en", 0.8, "английский текст вместо казахского"
            if sum(1 for w in lw if w in ES_PT_WORDS) >= 2:
                return "es/pt", 0.85, "испанский/португальский текст вместо казахского"
            if len(words) <= 3 and all(w[:1].isupper() for w in words):
                return None  # event/brand name kept in Latin ("SUMMER RUSH", "Grand Arena")
            return "lat", 0.55, "латиница вместо кириллицы при кириллическом исходнике"
        if not ctx.looks_native(text) and cyr >= 2:
            uz_hits = [w for w in lw if w in UZ_CYR_WORDS]
            if len(uz_hits) >= 2:
                return "uz", 0.75, f"узбекские слова: {', '.join(uz_hits[:3])}"
            ru_hits = [w for w in lw if w in RU_FUNC]
            if len(ru_hits) >= 2 and len(words) >= 3:
                return "ru", 0.8, f"русские служебные слова ({', '.join(ru_hits[:3])}) и ни одной казахской буквы"
        return None

    if exp in ("cyr", "lat") and len(words) >= 2:
        actual = "cyr" if cyr > lat else "lat" if lat > cyr else None
        if actual and actual != exp:
            if has_kk_letters(text):
                return "kk", 0.9, "текст с казахскими буквами в файле другого языка"
            if sum(1 for w in lw if w in RU_FUNC) >= 2:
                return "ru", 0.8, "русский текст при ожидаемой латинице"
            if sum(1 for w in lw if w in EN_WORDS) >= 2 and ctx.lang != "en":
                return "en", 0.75, "английский текст"
            return actual, 0.6, f"письменность {actual} при ожидаемой {exp}"
        if ctx.lang == "uz" and actual == "lat" and sum(1 for w in lw if w in EN_WORDS) >= 2 and not UZ_APOS.search(text):
            return "en", 0.7, "английские служебные слова в узбекском тексте"
    return None


def check_wrong_language(ctx: Context):
    if ctx.same_language:
        return
    for key, src, tgt in ctx.pairs():
        if norm(src) == norm(tgt):
            continue  # reported as same_as_source
        loc = _ref_hit(ctx, key, tgt, src)
        if loc:
            yield Finding(
                key, "wrong_language", "high", src, tgt,
                f"перевод дословно совпадает со строкой из файла локали {loc}",
                None, 0.97, rule="ref_match",
            )  # fmt: skip
            continue
        hit = _wrong_language(ctx, key, src, tgt)
        if hit:
            lang, conf, ev = hit
            sev = "high" if conf >= 0.75 else "medium"
            yield Finding(key, "wrong_language", sev, src, tgt, ev, None, conf, rule=f"script_markers:{lang}")


def check_mt_heuristics(ctx: Context):
    if ctx.lang != "kk":
        return
    for key, src, tgt in ctx.pairs():
        text = ctx.plain("tgt", key, " ")
        if not ctx.looks_native(text):
            continue  # not Kazakh text at all: wrong_language/same_as_source territory
        words = tokens(text)
        reasons: list[tuple[str, float]] = []
        ru_func = [w for w in words if w.casefold() in RU_FUNC]
        if ru_func:
            reasons.append((f"русские служебные слова в казахском тексте: {', '.join(ru_func[:3])}", 0.75))
        ru_forms = [
            w for w in words
            if word_script(w) == "cyr" and len(w) >= 5 and not any(c in KK_SPECIFIC for c in w)
            and w.casefold() not in ctx.keep_tokens and RU_MORPH.search(w.casefold())
        ]  # fmt: skip
        if ru_forms:
            reasons.append((f"русская словоформа без перевода: {', '.join(ru_forms[:3])}", 0.7))
        for pat, why in CALQUES:
            if pat.search(text):
                reasons.append((why, 0.45))
        if not reasons:
            continue
        conf = max(r[1] for r in reasons)
        sev = "medium" if conf >= 0.7 else "low"
        yield Finding(
            key, "machine_translation_suspect", sev, src, tgt,
            "; ".join(r[0] for r in reasons), None, conf, rule="mt_heuristics",
        )  # fmt: skip

