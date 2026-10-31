import csv
import json

from conftest import kk_data

from locqa.arb import ArbFile
from locqa.cli import main


def _fill(path, decisions):
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        if r["key"] in decisions:
            r.update(decisions[r["key"]])
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return rows


def test_full_review_cycle(files):
    kk = files["write"](
        "intl_kk.arb",
        kk_data(
            level_up="Жаңа деңгей: {lvl}",
            send_money_to="{name}-ға ақша жіберу",
            error_unknown="Белгісіз кате",
            player_kicked="Игрок исключён",
            garage_rent_button="Гаражды жалға алу үшін осы жерді басыңыз",
        ),
    )
    rep = files["dir"] / "report.json"
    review = files["dir"] / "review.csv"
    fixed = files["dir"] / "intl_kk_fixed.arb"
    main(["check", "--source", str(files["ru"]), "--target", str(kk), "--out", str(rep), "--html", "-"])
    assert main(["export-review", "--report", str(rep), "--out", str(review)]) == 0
    _fill(
        review,
        {
            "level_up": {"decision": "accept", "reviewer": "Айгерім"},
            "send_money_to": {"decision": "accept", "reviewer": "Айгерім"},
            "error_unknown": {"decision": "accept", "reviewer": ""},  # no reviewer -> not applied
            "player_kicked": {"decision": "edit", "final_text": "Ойыншы шығарылды", "reviewer": "Айгерім"},
            "garage_rent_button": {"decision": "edit", "final_text": "Жалға алу {x}", "reviewer": "Айгерім"},  # breaks placeholders
        },
    )
    rc = main(["apply", "--target", str(kk), "--review", str(review), "--source", str(files["ru"]), "--out", str(fixed)])
    assert rc == 0
    out = ArbFile.load(fixed)
    m = out.messages()
    assert m["level_up"] == "Жаңа деңгей: {level}"
    assert m["send_money_to"] == "{name} ойыншысына ақша жіберу"
    assert m["player_kicked"] == "Ойыншы шығарылды"
    assert m["error_unknown"] == "Белгісіз кате"
    assert m["garage_rent_button"] == "Гаражды жалға алу үшін осы жерді басыңыз"
    meta = out.meta("player_kicked")
    assert meta["x-reviewed"] is True and meta["x-reviewed-by"] == "Айгерім" and meta["x-original"] == "Игрок исключён"
    keys = list(out.data)
    assert keys.index("@player_kicked") == keys.index("player_kicked") + 1
    assert out.meta("shop_title") == {}  # untouched keys get no metadata

    side = json.loads((files["dir"] / "intl_kk_fixed.review.json").read_text(encoding="utf-8"))
    assert {c["key"] for c in side["changes"]} == {"level_up", "send_money_to", "player_kicked"}
    assert all(c["reviewed_by_human"] for c in side["changes"])
    assert any("garage_rent_button" in e for e in side["errors"])
    assert any("error_unknown" in e for e in side["errors"])

    assert main(["validate", str(fixed), "--source", str(files["ru"])]) == 0

    # reviewed flags flow back into the report
    rep2 = files["dir"] / "report2.json"
    main(["check", "--source", str(files["ru"]), "--target", str(kk), "--out", str(rep2), "--html", "-", "--review", str(review)])
    flags = {f["key"]: f["reviewed_by_human"] for f in json.loads(rep2.read_text(encoding="utf-8"))["findings"]}
    assert flags["level_up"] is True and flags["error_unknown"] is False
    # edit rejected by apply (broken placeholders) leaves the string unfixed -> not "reviewed"
    assert flags["garage_rent_button"] is False


def test_apply_keeps_existing_metadata(files):
    data = kk_data()
    data["@garage_rent_button"] = {"description": "kept"}
    kk = files["write"]("intl_kk.arb", data)
    review = files["dir"] / "r.csv"
    review.write_text(
        "key,decision,final_text,reviewer,issue_types\ngarage_rent_button,edit,Жалға беру,Ә,length_overflow\n",
        encoding="utf-8",
    )
    fixed = files["dir"] / "f.arb"
    main(["apply", "--target", str(kk), "--review", str(review), "--out", str(fixed)])
    meta = ArbFile.load(fixed).meta("garage_rent_button")
    assert meta["description"] == "kept" and meta["x-reviewed"] is True


def test_review_stats(files):
    from locqa.review import load_review
    from locqa.stats import review_stats

    review = files["dir"] / "r.csv"
    review.write_text(
        "key,issue_types,decision,final_text,reviewer\n"
        "a,orthography,accept,,Ә\n"
        "b,orthography,keep,,Ә\n"
        "c,length_overflow,edit,Жабу,Б\n"
        "d,length_overflow,,,\n"
        "e,same_as_source,accept,,\n",  # no reviewer: not counted as reviewed
        encoding="utf-8",
    )
    rows, errors = load_review(review)
    s = review_stats(rows)
    assert not errors
    assert s["reviewed_by_human"] == 3 and s["reviewers"] == ["Б", "Ә"]
    assert s["changed_strings"] == 2 and s["unsigned_decisions"] == 1
    assert s["precision_on_reviewed"] == round(2 / 3, 3)
    assert s["per_type"]["orthography"]["precision"] == 0.5
    assert s["per_type"]["length_overflow"]["precision"] == 1.0
    assert main(["review-stats", "--review", str(review)]) == 0


def test_format_preserving_apply_and_draft_mode(tmp_path):
    from locqa.review import ReviewRow, apply_review

    text = (
        '{\n  "@@locale": "kk",\n  "@@comment": "A",\n  "a": "бір",\n  "@@comment": "B",\n'
        '  "b": "екі",\n  "@b": {"description": "d"},\n  "a": "бір"\n}\n'
    )
    tgt = ArbFile.loads(text, "intl_kk.arb")
    rows = [
        ReviewRow("a", "edit", "БІР", "", "Ә", ["orthography"]),
        ReviewRow("b", "skip", "", "ЕКІ", "", ["orthography"]),  # suggestion only: draft mode
    ]
    res = apply_review(tgt, rows)
    assert res.text.count('"@@comment"') == 2  # repeated section markers survive
    assert res.text.count('"a": "БІР"') == 2  # every occurrence of a duplicated key is updated
    assert '"b": "екі"' in res.text  # not applied without --include-unreviewed
    draft = apply_review(tgt, rows, include_unreviewed=True)
    out = ArbFile.loads(draft.text)
    assert out.meta("b") == {"description": "d", "x-reviewed": False, "x-original": "екі", "x-issues": ["orthography"]}
    assert out.meta("a")["x-reviewed"] is True
    assert [c["reviewed_by_human"] for c in draft.changes] == [True, False]
    # untouched lines are byte-identical
    assert draft.text.splitlines()[0:3] == text.splitlines()[0:3]


def test_export_review_with_external_suggestions(files):
    kk = files["write"]("intl_kk.arb", kk_data(settings_title="Настройки ру", player_kicked="Игрок исключён"))
    rep = files["dir"] / "r.json"
    main(["check", "--source", str(files["ru"]), "--target", str(kk), "--out", str(rep), "--html", "-"])
    sug = files["dir"] / "s.json"
    sug.write_text(json.dumps({"player_kicked": {"suggestion": "Ойыншы шығарылды", "comment": "LLM"}}, ensure_ascii=False), encoding="utf-8")
    review = files["dir"] / "review.csv"
    main(["export-review", "--report", str(rep), "--suggestions", str(sug), "--out", str(review)])
    rows = {r["key"]: r for r in csv.DictReader(open(review, encoding="utf-8-sig"))}
    assert rows["player_kicked"]["suggestion"] == "Ойыншы шығарылды" and rows["player_kicked"]["comment"] == "LLM"


def test_export_review_adds_rows_found_by_reviewer(tmp_path):
    from locqa.model import Finding
    from locqa.review import export_review

    out = tmp_path / "r.csv"
    f = Finding("a", "orthography", "low", "x", "y", "ev", None, 0.9)
    extra = {"b": {"suggestion": "Неке", "source": "Брак", "target": "Тәртіп сақтау", "comment": "смысл"}}
    assert export_review([f], out, extra) == 2
    rows = {r["key"]: r for r in csv.DictReader(open(out, encoding="utf-8-sig"))}
    assert rows["b"]["issue_types"] == "machine_translation_suspect" and rows["b"]["suggestion"] == "Неке"
