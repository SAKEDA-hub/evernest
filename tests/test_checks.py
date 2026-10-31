from conftest import PAIRS, make_ctx

from locqa import engine


def run(overrides=None, **kw):
    ctx = make_ctx(overrides, **kw)
    return {(f.key, f.issue_type): f for f in engine.run(ctx)}, ctx


def test_clean_corpus_has_no_findings_even_below_threshold():
    found, ctx = run(refs=True)
    assert found == {}
    assert ctx.diagnostics == []


def test_untranslated_missing_and_empty():
    found, _ = run({"shop_title": None, "not_enough_money": "  ", "chat_title": ""})
    assert found[("shop_title", "untranslated")].evidence == "ключ отсутствует в целевом файле"
    assert found[("not_enough_money", "untranslated")].severity == "high"
    # one-word source: an empty piece may be intentional (word order), so softer
    assert found[("chat_title", "untranslated")].severity == "medium"


def test_same_as_source_vs_legit_matches():
    found, _ = run({"not_enough_money": "Недостаточно денег", "shop_title": "Магазин"})
    f = found[("not_enough_money", "same_as_source")]
    assert f.severity == "high" and f.confidence >= 0.9
    # "Магазин" is a known loan word; brands/numbers in the clean corpus are not flagged either
    assert ("shop_title", "same_as_source") not in found
    assert not any(k in ("server_name", "year", "faction_title") for k, _ in found)


def test_single_word_same_as_source():
    found, _ = run({"settings_title": "Настройки", "player_kicked": "Игрок исключён", "error_title": "Ошибка"})
    # a lone noun is usually a loan: reported below the default threshold
    assert found[("settings_title", "same_as_source")].confidence < 0.5
    # units/abbreviations are never reported
    found2, _ = run({"error_input": "кг"})
    assert ("error_input", "same_as_source") not in found2


def test_single_word_same_as_source_russian_form():
    found, _ = run({"job_police": "Полицейский"})
    assert found[("job_police", "same_as_source")].confidence >= 0.7


def test_placeholder_rename_with_suggestion():
    found, _ = run({"level_up": "Жаңа деңгей: {lvl}"})
    f = found[("level_up", "placeholder_mismatch")]
    assert "{level}" in f.evidence and "{lvl}" in f.evidence
    assert f.suggestion == "Жаңа деңгей: {level}"


def test_placeholder_cyrillic_homoglyph_is_fixed():
    found, _ = run({"version_label": "Нұсқа {vеrsion}"})  # Cyrillic е
    f = found[("version_label", "placeholder_mismatch")]
    assert f.suggestion == "Нұсқа {version}"


def test_plural_missing_other_and_unused_forms():
    found, _ = run({"items_count": "{count, plural, one{{count} зат} few{{count} зат}}"})
    f = found[("items_count", "plural_mismatch")]
    assert f.severity == "high"
    assert "other" in f.evidence and "few" in f.evidence


def test_plural_turned_into_simple_placeholder():
    found, _ = run({"items_count": "{count} зат"})
    assert found[("items_count", "plural_mismatch")].severity == "medium"


def test_glossary_brand_and_forbidden():
    found, _ = run({"coins_buy_button": "Гранд Коинс сатып алу"})
    f = found[("coins_buy_button", "glossary_violation")]
    assert f.severity == "high"
    assert f.suggestion == "Grand Coins сатып алу"


def test_glossary_missing_term():
    found, _ = run({"family_create_button": "Әулет құру"})
    assert ("family_create_button", "glossary_violation") in found
    # slang variant is allowed for «варн»
    found, _ = run({"warn_received": "Сіз әкімшіден варн алдыңыз"})
    assert ("warn_received", "glossary_violation") not in found


def test_length_overflow_button():
    found, _ = run({"garage_rent_button": "Гаражды жалға алу үшін осы жерді басыңыз"})
    f = found[("garage_rent_button", "length_overflow")]
    assert f.severity == "high"
    assert "кнопк" in f.evidence


def test_inconsistent_term():
    found, _ = run({"quest_new": "Жаңа міндет"})
    f = found[("quest_new", "inconsistent_term")]
    assert "тапсырма" in f.evidence


def test_suffix_harmony_with_rephrase():
    found, _ = run({"send_money_to": "{name}-ға ақша жіберу"})
    f = found[("send_money_to", "suffix_harmony")]
    assert f.severity == "high"
    assert f.suggestion == "{name} ойыншысына ақша жіберу"
    assert "needs_code_handling" not in f.tags


def test_suffix_harmony_numeric_needs_code():
    found, _ = run({"error_code": "Қате коды: {code}-ға"})
    assert ("error_code", "suffix_harmony") in found
    found, _ = run({"player_online": "Желідегі ойыншылар: {count}-дан көп"})
    f = found[("player_online", "suffix_harmony")]
    assert f.suggestion is None and "needs_code_handling" in f.tags


def test_suffix_rephrase_does_not_duplicate_player_word():
    found, _ = run({"send_money_to": "Ойыншы {name}-ға ақша жіберу"})
    assert found[("send_money_to", "suffix_harmony")].suggestion == "{name} ойыншысына ақша жіберу"


def test_orthography_russian_letter_for_kazakh():
    found, _ = run({"error_unknown": "Белгісіз кате"})
    f = found[("error_unknown", "orthography")]
    assert "қате" in f.evidence
    assert f.suggestion == "Белгісіз қате"


def test_orthography_mixed_script():
    found, _ = run({"error_title": "Қaте"})  # Latin a
    f = found[("error_title", "orthography")]
    assert f.suggestion == "Қате"


def test_orthography_typography():
    found, _ = run({"error_network": "Желі қатесі . Қайталап  көріңіз."})
    f = found[("error_network", "orthography")]
    assert f.severity == "low"
    assert f.suggestion == "Желі қатесі. Қайталап көріңіз."


def test_wrong_language_via_reference_file():
    found, _ = run({"confirm_button": "Tasdiqlash"}, refs=True)
    f = found[("confirm_button", "wrong_language")]
    assert f.confidence >= 0.95 and "uz" in f.evidence


def test_wrong_language_markers_without_refs():
    found, _ = run({"not_enough_money": "Pul yetarli emas, oʻyinchi"})
    assert found[("not_enough_money", "wrong_language")].rule == "script_markers:uz"
    found, _ = run({"player_not_found": "Игрок с таким ником не был найден"})
    assert found[("player_not_found", "wrong_language")].rule == "script_markers:ru"
    found, _ = run({"logout_confirm": "Are you sure you want to log out?"})
    assert found[("logout_confirm", "wrong_language")].rule == "script_markers:en"
    found, _ = run({"shop_title": "Дўкон ҳам"})
    assert found[("shop_title", "wrong_language")].rule == "script_markers:uz"


def test_machine_translation_suspect():
    found, _ = run({"family_invite": "{name} сізді отбасына шақырады и күтеді"})
    assert ("family_invite", "machine_translation_suspect") in found
    found, _ = run({"quest_done": "Тапсырма выполнено"})
    assert "выполнено" in found[("quest_done", "machine_translation_suspect")].evidence


def test_diagnostics_extra_and_orphan():
    _, ctx = run({"zzz_extra": "қосымша", "@ghost": {"description": "x"}})
    codes = {d.code for d in ctx.diagnostics}
    assert {"extra_key", "orphan_metadata"} <= codes


def test_every_finding_has_explainable_fields():
    over = {
        "shop_title": None,
        "level_up": "Жаңа деңгей: {lvl}",
        "send_money_to": "{name}-ға ақша жіберу",
        "error_unknown": "Белгісіз кате",
        "garage_rent_button": "Гаражды жалға алу үшін осы жерді басыңыз",
    }
    found, _ = run(over)
    assert len(found) >= len(over)
    for f in found.values():
        d = f.to_dict()
        assert d["severity"] in ("high", "medium", "low")
        assert d["evidence"] and 0 <= d["confidence"] <= 1
        assert set(d) >= {"key", "issue_type", "severity", "source", "target", "evidence", "suggestion", "confidence", "reviewed_by_human"}


def test_corpus_sanity():
    assert len(PAIRS) > 60


def test_orthography_uses_external_vocab_for_rare_words():
    over = {"error_network": "Желі қатесі. Кайталап көріңіз."}
    found, _ = run(over)
    assert ("error_network", "orthography") not in found  # «қайталап» occurs once: no evidence
    found, _ = run(over, vocab={"қайталап"})
    assert found[("error_network", "orthography")].suggestion == "Желі қатесі. Қайталап көріңіз."


def test_project_glossary_is_valid_and_quiet_on_clean_corpus():
    from pathlib import Path

    from locqa.glossary import Glossary

    g = Glossary.load(Path(__file__).parent.parent / "glossary.csv")
    assert len(g) >= 20
    # every row parsed into the right columns (an unquoted "{0,4}" once shifted them)
    assert all(e.type in ("term", "ui", "slang", "brand") for e in g.entries)
    assert all(e.status in ("draft", "observed", "from_brief", "verified") for e in g.entries)
    ctx = make_ctx(gl=False)
    ctx.glossary = g
    assert [f for f in engine.run(ctx) if f.issue_type == "glossary_violation"] == []


def test_orthography_uses_inflection_family_when_fix_changes_word_start():
    # «қате» appears as қате/қатесі across keys; a lone «Кате» is still caught
    found, _ = run({"error_title": "Кате"})
    assert found[("error_title", "orthography")].suggestion == "Қате"


def test_printf_placeholders():
    found, _ = run({"error_code": "Қате коды"}, )
    # {code} missing -> ICU set mismatch
    assert ("error_code", "placeholder_mismatch") in found
    from locqa.arb import ArbFile
    from locqa.context import Context

    src = ArbFile.from_dict({"@@locale": "ru", "a": "%s ур.", "b": "Осталось %d из %d"})
    tgt = ArbFile.from_dict({"@@locale": "kk", "a": "%s деңг.", "b": "%d қалды"})
    out = {(f.key, f.issue_type) for f in engine.run(Context(src, tgt))}
    assert ("b", "placeholder_mismatch") in out and ("a", "placeholder_mismatch") not in out


def test_placeholder_braces_lost_gets_suggestion():
    found, _ = run({"version_label": "Нұсқа version"})
    f = found[("version_label", "placeholder_mismatch")]
    assert f.suggestion == "Нұсқа {version}" and "без фигурных скобок" in f.evidence


def test_number_hyphen_noun_is_not_a_suffix():
    found, _ = run({"player_online": "Желідегі ойыншылар: {count}-орын", "level_up": "Жаңа деңгей: {level}-ші"})
    assert ("player_online", "suffix_harmony") not in found  # "5-орын" is standard spelling
    assert ("level_up", "suffix_harmony") in found  # ordinal ending depends on the number


def test_latin_event_names_and_portuguese():
    found, _ = run({"shop_title": "SUMMER RUSH", "logout_confirm": "Por favor, abra o aplicativo novamente"})
    assert ("shop_title", "wrong_language") not in found
    assert found[("logout_confirm", "wrong_language")].rule == "script_markers:es/pt"


def test_hyphenated_latin_name_with_kazakh_suffix_is_fine():
    found, _ = run({"coins_buy_button": "Grand Coins-ті сатып алу"})
    assert ("coins_buy_button", "orthography") not in found


def test_adequacy_flags_swapped_translation():
    # «Отбасы» (family) where the source says «Задания» (quests): the source word is well
    # attested and «отбасы» clearly belongs to «семья» elsewhere in the file
    found, _ = run({"quest_title": "Отбасы"})
    f = found.get(("quest_title", "machine_translation_suspect"))
    assert f is not None and f.confidence < 0.5 and "семь" in f.evidence


def test_glossary_exact_ignores_quotes_and_trailing_punctuation():
    from locqa.glossary import Glossary, GlossaryEntry

    g = Glossary([GlossaryEntry(ru="магазин", kk="дүкен")])
    for s in ("Магазин", "Магазин.", "«Магазин»", '"Магазин"', "„Магазин“"):
        assert g.exact(s) == "дүкен", s
    assert g.exact("Магазин закрыт") is None
