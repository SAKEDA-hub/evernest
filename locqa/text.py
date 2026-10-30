"""Text helpers: scripts, tokens, visible length, light stemming, case-preserving replace."""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

KK_SPECIFIC = frozenset("әғқңөұүһіӘҒҚҢӨҰҮҺІ")
UZ_CYR_SPECIFIC = frozenset("ўҳЎҲ")
WORD_RE = re.compile(r"[^\W\d_]+(?:[-'ʼʻ’`][^\W\d_]+)*")


def char_script(ch: str) -> str | None:
    o = ord(ch)
    if 0x0400 <= o <= 0x052F or 0x1C80 <= o <= 0x1C8F or 0x2DE0 <= o <= 0x2DFF or 0xA640 <= o <= 0xA69F:
        return "cyr"
    if ch in "×÷":
        return None
    if ("a" <= ch <= "z") or ("A" <= ch <= "Z") or 0x00C0 <= o <= 0x024F or 0x1E00 <= o <= 0x1EFF:
        return "lat"
    if ch.isalpha():
        return "other"
    return None


def word_script(word: str) -> str | None:
    scripts = {s for s in map(char_script, word) if s}
    if not scripts:
        return None
    if "cyr" in scripts and "lat" in scripts:
        return "mixed"
    if len(scripts) == 1:
        return next(iter(scripts))
    return "other"


def tokens(text: str) -> list[str]:
    return WORD_RE.findall(text)


def norm(text: str | None) -> str:
    return " ".join((text or "").split()).casefold()


def vlen(text: str) -> int:
    """Visible length: code points minus combining marks."""
    return sum(1 for c in text if not unicodedata.combining(c))


def script_counts(text: str) -> Counter:
    return Counter(word_script(w) for w in tokens(text))


def dominant_script(text: str) -> str | None:
    c = script_counts(text)
    cyr, lat = c.get("cyr", 0), c.get("lat", 0)
    if cyr == lat == 0:
        return None
    return "cyr" if cyr >= lat else "lat"


def has_kk_letters(text: str) -> bool:
    return any(ch in KK_SPECIFIC for ch in text)


def split_key(key: str) -> list[str]:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    return [p for p in re.split(r"[^A-Za-z0-9]+", s.lower()) if p]


_RU_SUFFIXES = sorted(
    "иями ями ами ией иях ого его ому ему ыми ими ий ый ой ая яя ое ее ые ие ых их ую юю ом ем "
    "ам ям ах ях ов ев ей ия ии ию ье ья ью ьи а я у ю ы и е о ь".split(),
    key=len,
    reverse=True,
)


def ru_stem(word: str) -> str:
    """Crude Russian stemmer: strips one inflectional ending, keeps >= 3 chars."""
    w = word.casefold().replace("ё", "е")
    for suf in _RU_SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


def kk_stem(word: str) -> str:
    """Kazakh is suffixing: a word's dictionary form is a prefix of its inflections, except
    final п/қ/к voicing before vowels (кітап→кітабы, тарақ→тарағы, жүрек→жүрегі)."""
    w = word.casefold()
    if len(w) >= 5 and w[-1] in "пқк":
        return w[:-1]
    if len(w) >= 5 and w[-1] == w[-2]:
        return w[:-1]  # loans drop a doubled consonant before suffixes: прогресс -> прогресі
    return w


def match_case(src: str, new: str) -> str:
    if len(src) > 1 and src.isupper():
        return new.upper()
    if src[:1].isupper():
        return new[:1].upper() + new[1:]
    return new


def replace_word(text: str, old: str, new: str, count: int = 0) -> str:
    pat = re.compile(r"(?<!\w)" + re.escape(old) + r"(?!\w)", re.IGNORECASE)
    return pat.sub(lambda m: match_case(m.group(0), new), text, count=count)
