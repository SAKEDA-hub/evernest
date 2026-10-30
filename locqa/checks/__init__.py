"""Check registry. Each check is `fn(ctx) -> Iterable[Finding]`."""
from . import adequacy, lang_checks, length, structural, terms

REGISTRY = {
    "untranslated": structural.check_untranslated,
    "same_as_source": structural.check_same_as_source,
    "icu": structural.check_icu,  # placeholder_mismatch + plural_mismatch
    "glossary": terms.check_glossary,
    "inconsistent_term": terms.check_inconsistent,
    "length": length.check_length,
    "suffix_harmony": lang_checks.check_suffix_harmony,
    "orthography": lang_checks.check_orthography,
    "wrong_language": lang_checks.check_wrong_language,
    "mt_heuristics": lang_checks.check_mt_heuristics,
    "adequacy": adequacy.check_adequacy,
}
