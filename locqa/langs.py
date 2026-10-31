"""Locale handling and per-language profiles."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ISO639_1 = frozenset(
    "af am ar as az be bg bn bs ca cs cy da de el en es et eu fa fi fil fr ga gl gu he hi hr hu "
    "hy id is it ja ka kk km kn ko ky lo lt lv mk ml mn mr ms my nb ne nl nn no pa pl ps pt ro ru "
    "si sk sl sq sr sv sw ta te tg th tk tl tr tt ug uk ur uz vi zh".split()
)

# gen-l10n `_isValidGetterAndMethodName`: template keys become Dart getters (no leading
# capital, digit or underscore; only letters, digits, `_`, `$`). gen-l10n aborts otherwise.
DART_GETTER = re.compile(r"[a-z$][A-Za-z0-9_$]*")


def invalid_resource_names(keys) -> list[str]:
    return [k for k in keys if not DART_GETTER.fullmatch(k)]


# CLDR cardinal plural categories ("other" is always implied).
PLURAL_CATEGORIES: dict[str, frozenset[str]] = {
    "ru": frozenset({"one", "few", "many", "other"}),
    "uk": frozenset({"one", "few", "many", "other"}),
    "be": frozenset({"one", "few", "many", "other"}),
    "pl": frozenset({"one", "few", "many", "other"}),
    "kk": frozenset({"one", "other"}),
    "uz": frozenset({"one", "other"}),
    "ky": frozenset({"one", "other"}),
    "tr": frozenset({"one", "other"}),
    "az": frozenset({"one", "other"}),
    "en": frozenset({"one", "other"}),
    "de": frozenset({"one", "other"}),
    "es": frozenset({"one", "many", "other"}),
    "pt": frozenset({"one", "many", "other"}),
    "fr": frozenset({"one", "many", "other"}),
    "it": frozenset({"one", "many", "other"}),
    "ja": frozenset({"other"}),
    "ko": frozenset({"other"}),
    "zh": frozenset({"other"}),
    "ar": frozenset({"zero", "one", "two", "few", "many", "other"}),
}


@dataclass(frozen=True)
class LangProfile:
    code: str
    name: str
    script: str | None  # expected script: "cyr" | "lat" | None (unknown)
    plural_categories: frozenset[str] | None


PROFILES = {
    "kk": LangProfile("kk", "казахский", "cyr", PLURAL_CATEGORIES["kk"]),
    "uz": LangProfile("uz", "узбекский", "lat", PLURAL_CATEGORIES["uz"]),
    "ru": LangProfile("ru", "русский", "cyr", PLURAL_CATEGORIES["ru"]),
    "en": LangProfile("en", "английский", "lat", PLURAL_CATEGORIES["en"]),
    "es": LangProfile("es", "испанский", "lat", PLURAL_CATEGORIES["es"]),
    "pt": LangProfile("pt", "португальский", "lat", PLURAL_CATEGORIES["pt"]),
}


def base_locale(loc: str | None) -> str:
    if not loc:
        return ""
    return re.split(r"[_-]", loc.strip())[0].lower()


def get_profile(loc: str | None) -> LangProfile:
    b = base_locale(loc)
    if b in PROFILES:
        return PROFILES[b]
    return LangProfile(b or "und", b or "unknown", None, PLURAL_CATEGORIES.get(b))


def locale_from_filename(name: str) -> str | None:
    """intl_kk.arb -> kk, app_en_US.arb -> en_US, intl_kk_fixed.arb -> kk."""
    stem = Path(name).stem
    parts = stem.split("_")
    for i, part in enumerate(parts[1:], start=1):
        if part.lower() in ISO639_1:
            nxt = parts[i + 1] if i + 1 < len(parts) else ""
            if re.fullmatch(r"[A-Z]{2}|[A-Z][a-z]{3}", nxt):
                return f"{part.lower()}_{nxt}"
            return part.lower()
    if parts and parts[0].lower() in ISO639_1 and len(parts) == 1:
        return parts[0].lower()
    return None
