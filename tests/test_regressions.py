"""Regression tests for issues found when running locqa on the real handout (data itself is not used)."""
import csv
import json

from conftest import kk_data, ru_data
from test_checks import run

from locqa.cli import main
from locqa.glossary import GlossaryEntry
from locqa.langs import invalid_resource_names


# --- gen-l10n resource names -----------------------------------------------------------


def test_invalid_resource_names_mirror_gen_l10n():
    ok = ["shop_title", "launcherV2Title", "a1", "$x", "tune_buy_neon"]
    bad = ["UNeedHelp", "_private", "1st", "inventoryEquipped@description", "кнопка"]
    assert invalid_resource_names(ok + bad) == bad


def test_validate_reports_template_names(files, capsys):
    ru = files["write"]("intl_ru.arb", {**ru_data(), "UNeedHelp": "Помощь"})
    # validating the template itself: gen-l10n would abort -> failure
    assert main(["validate", str(ru)]) == 1
    assert "UNeedHelp" in capsys.readouterr().out
    # validating a translation against it: warning only, the target is not at fault
    assert main(["validate", str(files["kk"]), "--source", str(ru)]) == 0
    out = capsys.readouterr().out
    assert "предупреждение (шаблон" in out and "UNeedHelp" in out


def test_validate_locale_mismatch_fails(files):
    kk = files["write"]("intl_kk.arb", {**kk_data(), "@@locale": "uz"})
    assert main(["validate", str(kk), "--source", str(files["ru"])]) == 1


def test_check_reports_invalid_template_name_as_diagnostic(files):
    ru = files["write"]("intl_ru.arb", {**ru_data(), "TowConfirmDesc": "Эвакуировать?"})
    out = files["dir"] / "r.json"
    assert main(["check", "--source", str(ru), "--target", str(files["kk"]), "--out", str(out), "--html", "-"]) == 0
    diags = json.loads(out.read_text(encoding="utf-8"))["diagnostics"]
    assert any(d["code"] == "invalid_resource_name" and d["key"] == "TowConfirmDesc" for d in diags)


# --- detectors -------------------------------------------------------------------------


def test_forbidden_term_fix_keeps_inflection():
    e = GlossaryEntry("игрок", "ойыншы", kk_forbidden=["ойнаушы"])
    assert e.fix_forbidden("басқа ойнаушыларға беру") == "басқа ойыншыларға беру"
    assert e.fix_forbidden("ОЙНАУШЫЛАРДЫ іздеу") == "ОЙЫНШЫЛАРДЫ іздеу"
    assert e.fix_forbidden("ойыншы") is None


def test_mt_false_friend_needs_source_trigger():
    found, _ = run({"inventory_title": "Түгендеу", "cancel_button": "Болдыру"})
    f = found[("inventory_title", "machine_translation_suspect")]
    assert "инвентарь" in f.evidence and f.confidence >= 0.75
    assert ("cancel_button", "machine_translation_suspect") in found  # «Отмена» -> «Болдыру»
    # the same Kazakh word without the Russian trigger is fine
    found2, _ = run({"error_input": "Түгендеу"})
    assert ("error_input", "machine_translation_suspect") not in found2


def test_mt_heuristics_skip_proper_names_copied_from_source():
    over = {"quest_reward": "Вишневский берген тапсырма үшін сыйлық"}
    src = {"quest_reward": "Награда за задание от Вишневский"}
    found, _ = run(over, src_overrides=src)
    assert ("quest_reward", "machine_translation_suspect") not in found


def test_same_as_source_russian_infinitive_is_reported():
    found, _ = run({"close_button": "Закрыть"})
    f = found[("close_button", "same_as_source")]
    assert f.confidence >= 0.5 and "словоформа" in f.evidence


# --- review merged back into the report ------------------------------------------------


def _review_csv(path, rows):
    cols = ["key", "issue_types", "severity", "confidence", "source", "target", "suggestion",
            "evidence", "decision", "final_text", "reviewer", "comment"]  # fmt: skip
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def test_check_with_review_drops_rejected_and_adds_review_findings(files):
    kk = files["write"]("intl_kk.arb", kk_data(shop_title=None, level_up="Жаңа деңгей: {lvl}"))
    review = files["dir"] / "review.csv"
    _review_csv(
        review,
        [
            {"key": "shop_title", "issue_types": "untranslated", "decision": "edit", "final_text": "Дүкен", "reviewer": "А"},
            {"key": "level_up", "issue_types": "placeholder_mismatch", "decision": "keep", "reviewer": "А"},
            # not flagged by the tool: semantic error found by the reviewer
            {"key": "error_input", "issue_types": "machine_translation_suspect", "severity": "medium",
             "confidence": "0.9", "suggestion": "Енгізу қатесі", "decision": "accept", "reviewer": "А",
             "evidence": "[добавлено ревью] смысл не тот"},
            # proposal nobody reviewed yet
            {"key": "quest_new", "issue_types": "machine_translation_suspect", "confidence": "0.6",
             "suggestion": "Жаңа тапсырма!", "comment": "LLM"},
        ],
    )  # fmt: skip
    out = files["dir"] / "r.json"
    rc = main(["check", "--source", str(files["ru"]), "--target", str(kk), "--review", str(review), "--out", str(out), "--html", "-"])
    assert rc == 0
    rep = json.loads(out.read_text(encoding="utf-8"))
    by = {(f["key"], f["issue_type"]): f for f in rep["findings"]}
    assert by[("shop_title", "untranslated")]["reviewed_by_human"] is True
    assert ("level_up", "placeholder_mismatch") not in by  # reviewer: false positive
    added = by[("error_input", "machine_translation_suspect")]
    assert added["reviewed_by_human"] is True and added["suggestion"] == "Енгізу қатесі"
    assert added["source"] == "Неверный ввод" and added["evidence"].startswith("найдено при ревью: смысл")
    assert by[("quest_new", "machine_translation_suspect")]["reviewed_by_human"] is False
    assert rep["summary"]["review"]["rejected_by_reviewer"] == 1
    assert rep["summary"]["review"]["added_by_review"] == 2


def test_length_extreme_growth_in_sentence_parts():
    src = {"timerAfterPrefix": "через:", "confirmBuyEnd": "кейс?"}
    over = {
        "timerAfterPrefix": "кейін: режимін қосу және жалғастыру",  # junk appended after «:»
        "confirmBuyEnd": "кейсті сатып алғыңыз келетініне сенімдісіз бе?",  # predicate moved here: fine
    }
    found, _ = run(over, src_overrides=src)
    f = found[("timerAfterPrefix", "length_overflow")]
    assert f.rule == "length_extreme" and "после завершающего «:»" in f.evidence
    assert ("confirmBuyEnd", "length_overflow") not in found
