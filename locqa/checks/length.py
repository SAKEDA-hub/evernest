"""length_overflow: translation much longer than the source in short UI elements.

No widget widths are available, so the expected length is source length times the
file-wide median target/source ratio of short strings (Kazakh is naturally longer than
Russian; a fixed constant would flag half the file). The element kind is inferred from
the key name and the source description.
"""
from __future__ import annotations

import re
import statistics

from ..context import Context
from ..model import Finding
from ..text import norm, split_key, tokens, vlen

BUTTON_TOKENS = frozenset("button btn cta tab tabs chip badge toggle".split())
# action verbs mark a button only when nothing in the key says "this is a message"
ACTION_TOKENS = frozenset(
    "ok cancel confirm close save apply accept decline buy sell submit next back continue retry menu".split()
)
MESSAGE_TOKENS = frozenset(
    "success error fail failed message msg text desc description info toast notification body "
    "tooltip dialog alert warning hint subtitle".split()
)
TITLE_TOKENS = frozenset("title header heading caption label hint subtitle".split())
DESC_BUTTON = re.compile(r"кнопк|button|вкладк|\btab\b|пункт меню", re.IGNORECASE)
DESC_TITLE = re.compile(r"заголов|title|header|подпис|label", re.IGNORECASE)
PH = "XXXXXX"
PAIR_PART = re.compile(r"(Start|End|Prefix|Suffix|Part\d*|_start|_end|_prefix|_suffix)$")  # placeholders count as ~6 visible chars on both sides


def ui_kind(key: str, description: str = "") -> tuple[str | None, str]:
    parts = split_key(key)
    for p in parts:
        if p in BUTTON_TOKENS:
            return "button", f"ключ похож на кнопку/вкладку (токен «{p}»)"
    if not MESSAGE_TOKENS.intersection(parts):
        for p in parts:
            if p in ACTION_TOKENS:
                return "button", f"ключ похож на кнопку действия (токен «{p}»)"
    for p in parts:
        if p in TITLE_TOKENS:
            return "title", f"ключ похож на заголовок/подпись (токен «{p}»)"
    if description and DESC_BUTTON.search(description):
        return "button", "описание ключа указывает на кнопку/вкладку"
    if description and DESC_TITLE.search(description):
        return "title", "описание ключа указывает на заголовок"
    return None, ""


def _target_len(ctx: Context, key: str) -> int:
    text = ctx.plain("tgt", key, PH)
    n = vlen(text)
    if ctx.options.translit and ctx.lang == "kk":
        from ..translit import translit_kk

        n = max(n, vlen(translit_kk(text)))
    return n


def expected_ratio(ctx: Context) -> tuple[float, int]:
    ratios = []
    for key, src, tgt in ctx.pairs():
        s = vlen(ctx.plain("src", key, PH))
        if 3 <= s <= 30 and norm(src) != norm(tgt):
            ratios.append(vlen(ctx.plain("tgt", key, PH)) / s)
    if len(ratios) < 20:
        return 1.25, len(ratios)
    return min(max(statistics.median(ratios), 1.0), 1.8), len(ratios)


EXTREME = 3.5  # x the expected length AND +18 chars: no Kazakh rewording of a <=20-char label grows that much
END_PUNCT = ":.!?"


def _extreme(ctx: Context, key, src, tgt, s_plain, sl, tl, exp, r, n_samples) -> Finding | None:
    ev = (
        f"{tl} символов против {sl} в исходнике — в {tl / exp:.1f} раза больше ожидаемого "
        f"(медианный коэффициент {r:.2f} по {n_samples} коротким строкам)"
    )
    if PAIR_PART.search(key):
        # part of a split sentence: Kazakh may legitimately move the whole predicate here
        # («кейс?» -> «...сатып алғыңыз келетініне сенімдісіз бе?»). Report only when the text
        # runs on past the source's closing punctuation: «через:» -> «кейін: режимін қосу...».
        p = s_plain.rstrip()[-1:]
        t = ctx.plain("tgt", key, PH).rstrip()
        if not (p and p in END_PUNCT and p in t and not t.endswith(p)):
            return None
        ev += f"; часть составной фразы, но текст продолжается после завершающего «{p}» исходника"
        conf = 0.65
    else:
        ev += "; для короткой строки интерфейса такой рост не объясняется порядком слов"
        conf = 0.75
    kind, _ = ui_kind(key, ctx.description(key))
    sev = "high" if kind == "button" else "medium"
    return Finding(key, "length_overflow", sev, src, tgt, ev, None, conf, rule="length_extreme")


def check_length(ctx: Context):
    r, n_samples = expected_ratio(ctx)
    for key, src, tgt in ctx.pairs():
        s_plain = ctx.plain("src", key, PH)
        sl = vlen(s_plain)
        if sl == 0:
            continue
        tl = _target_len(ctx, key)
        exp = sl * r
        # fallback for strings the main rule skips (sentence parts, labels ending with «:»)
        extreme = sl <= 20 and tl >= max(exp * EXTREME, sl + 18)
        if PAIR_PART.search(key):
            # Start/End halves of one sentence: Kazakh word order moves text between them
            if extreme and (f := _extreme(ctx, key, src, tgt, s_plain, sl, tl, exp, r, n_samples)):
                yield f
            continue
        kind, reason = ui_kind(key, ctx.description(key))
        weak = False
        # Kazakh UI text is routinely ~2x a terse Russian label ("Ваши призы" -> "Сіздің
        # сыйлықтарыңыз"); only clearly abnormal growth is reported.
        if kind == "button":
            limit = max(exp * 2.0, sl + 10)
        elif kind == "title":
            limit = max(exp * 2.2, sl + 12)
        elif sl <= 20 and len(tokens(s_plain)) <= 3 and not s_plain.rstrip().endswith((".", "!", "?", ":")):
            limit = max(exp * 2.5, sl + 15)
            weak = True
            reason = "короткая строка без точки — вероятно, элемент интерфейса"
        else:
            if extreme and (f := _extreme(ctx, key, src, tgt, s_plain, sl, tl, exp, r, n_samples)):
                yield f
            continue
        if tl <= limit:
            continue
        over = tl / exp
        if weak:
            sev, conf = ("medium", 0.7) if over >= 3 else ("low", 0.55)
        elif kind == "button":
            sev, conf = ("high" if over >= 3 else "medium"), 0.85
        else:
            sev, conf = ("medium" if over >= 3 else "low"), 0.75
        ev = (
            f"{tl} символов против {sl} в исходнике (ожидаемо ≈{exp:.0f} при медианном "
            f"коэффициенте {r:.2f} по {n_samples} коротким строкам); {reason}"
        )
        sugg = ctx.glossary.exact(src)
        if sugg and vlen(sugg) >= tl:
            sugg = None
        yield Finding(key, "length_overflow", sev, src, tgt, ev, sugg, conf, rule="length_ratio")
