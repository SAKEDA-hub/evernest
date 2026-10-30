import json

from conftest import kk_data

from locqa.cli import main


def test_check_end_to_end(files, capsys):
    kk = files["write"](
        "intl_kk.arb",
        kk_data(
            shop_title=None,
            level_up="Жаңа деңгей: {lvl}",
            send_money_to="{name}-ға ақша жіберу",
            cancel_button="<script>alert(1)</script>",
        ),
    )
    out = files["dir"] / "report.json"
    rc = main(
        [
            "check", "--source", str(files["ru"]), "--target", str(kk), "--glossary", str(files["glossary"]),
            "--ref", str(files["uz"]), "--ref", str(files["en"]), "--out", str(out),
        ]
    )  # fmt: skip
    assert rc == 0
    rep = json.loads(out.read_text(encoding="utf-8"))
    types = {(f["key"], f["issue_type"]) for f in rep["findings"]}
    assert {("shop_title", "untranslated"), ("level_up", "placeholder_mismatch"), ("send_money_to", "suffix_harmony")} <= types
    assert rep["target"]["locale"] == "kk" and rep["summary"]["findings"] == len(rep["findings"])

    html = (files["dir"] / "report.html").read_text(encoding="utf-8")
    assert "<script>alert(1)" not in html  # user strings cannot break out of the data block
    assert "\\u003cscript>alert(1)" in html


def test_check_flat_and_fail_on(files):
    kk = files["write"]("intl_kk.arb", kk_data(shop_title=None))
    out = files["dir"] / "r.json"
    rc = main(["check", "--source", str(files["ru"]), "--target", str(kk), "--out", str(out), "--flat", "--html", "-", "--fail-on", "high"])
    assert rc == 1
    data = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(data, list) and data[0]["issue_type"] == "untranslated"
    assert not (files["dir"] / "r.html").exists()


def test_check_clean_passes_fail_on(files):
    out = files["dir"] / "r.json"
    assert main(["check", "--source", str(files["ru"]), "--target", str(files["kk"]), "--out", str(out), "--fail-on", "low"]) == 0


def test_validate(files):
    assert main(["validate", str(files["kk"]), "--source", str(files["ru"])]) == 0
    bad = files["write"]("bad_kk.arb", kk_data(level_up="Жаңа деңгей: {lvl}", items_count="{count, plural, one{x}}"))
    assert main(["validate", str(bad), "--source", str(files["ru"])]) == 1


def test_missing_file_is_usage_error(files, capsys):
    assert main(["validate", str(files["dir"] / "nope.arb")]) == 1
    assert main(["check", "--source", str(files["dir"] / "nope.arb"), "--target", str(files["kk"]), "--out", "x.json"]) == 2


def test_uzbek_target(files):
    uz_full = {"@@locale": "uz", "confirm_button": "Tasdiqlash", "cancel_button": "Отмена покупки сейчас", "player_profile_title": "Oʻyinchi profili"}
    uz = files["write"]("intl_uz.arb", uz_full)
    out = files["dir"] / "uz.json"
    main(["check", "--source", str(files["ru"]), "--target", str(uz), "--out", str(out), "--html", "-"])
    rep = json.loads(out.read_text(encoding="utf-8"))
    types = {(f["key"], f["issue_type"]) for f in rep["findings"]}
    assert ("cancel_button", "same_as_source") not in types  # different text
    assert ("cancel_button", "wrong_language") in types
    assert ("shop_title", "untranslated") in types
    assert not any(t == "suffix_harmony" for _, t in types)  # Kazakh-only rule


def test_mine_terms(files):
    out = files["dir"] / "terms.csv"
    assert main(["mine-terms", "--source", str(files["ru"]), "--target", str(files["kk"]), "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8-sig")
    assert "задание" in text and "тапсырма" in text
