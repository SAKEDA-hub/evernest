"""Kazakh Cyrillic -> Latin transliteration (bonus mode).

The default table follows the 2021 draft of the official Kazakh Latin alphabet as we
understand it; it has NOT been verified against the normative text and must be checked by
a native speaker before the output is shown to players. Pass a JSON table
({"ә": "ä", ...}, lowercase keys) to `load_table` to override.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import icu
from .arb import ArbFile

KK_LATIN_2021 = {
    "а": "a", "ә": "ä", "б": "b", "в": "v", "г": "g", "ғ": "ğ", "д": "d", "е": "e", "ё": "io",
    "ж": "j", "з": "z", "и": "i", "й": "i", "к": "k", "қ": "q", "л": "l", "м": "m", "н": "n",
    "ң": "ñ", "о": "o", "ө": "ö", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ұ": "ū",
    "ү": "ü", "ф": "f", "х": "h", "һ": "h", "ц": "ts", "ч": "ch", "ш": "ş", "щ": "şş", "ъ": "",
    "ы": "y", "і": "ı", "ь": "", "э": "e", "ю": "iu", "я": "ia",
}  # fmt: skip

# Turkish-style dotted/dotless pair: и/й -> i, upper İ; і -> ı, upper I (str.upper("i") would give
# "I" and merge И with І). Verified against the April 2021 table; ч -> "ch" is NOT verified.
UPPER = {"i": "İ", "ı": "I"}

_table = dict(KK_LATIN_2021)


def load_table(path: str | Path) -> None:
    global _table
    _table = {k.lower(): v for k, v in json.loads(Path(path).read_text(encoding="utf-8")).items()}


def translit_kk(text: str) -> str:
    out = []
    for ch in text:
        low = ch.lower()
        if low in _table:
            rep = _table[low]
            if ch != low and rep:
                head = UPPER.get(rep[:1], rep[:1].upper())
                rep = head + rep[1:]
            out.append(rep)
        else:
            out.append(ch)
    return "".join(out)


def translit_arb(arb: ArbFile, escaping: bool = False, locale: str = "kk_Latn") -> tuple[ArbFile, list[str]]:
    """Transliterate literal text only; placeholders and ICU keywords are untouched."""
    data: dict = {}
    errors: list[str] = []
    for k, v in arb.data.items():
        if k == "@@locale":
            data[k] = locale
        elif not k.startswith("@") and isinstance(v, str):
            try:
                data[k] = icu.transform_text(v, translit_kk, escaping)
            except icu.IcuError as e:
                errors.append(f"{k}: {e}")
                data[k] = v
        else:
            data[k] = v
    return ArbFile(data, None, [], arb.indent), errors
