import pytest

from locqa.arb import ArbError, ArbFile
from locqa.langs import locale_from_filename


def test_duplicates_bom_and_order(tmp_path):
    p = tmp_path / "intl_kk.arb"
    p.write_bytes('﻿{\n    "b": "1",\n    "a": "2",\n    "b": "3"\n}'.encode())
    f = ArbFile.load(p)
    assert f.duplicates == ["b"]
    assert list(f.messages()) == ["b", "a"]
    assert f.indent == 4
    assert f.dumps().startswith('{\n    "b": "3"')


def test_invalid_json_and_non_object(tmp_path):
    (tmp_path / "x.arb").write_text("{", encoding="utf-8")
    with pytest.raises(ArbError):
        ArbFile.load(tmp_path / "x.arb")
    with pytest.raises(ArbError):
        ArbFile.loads("[]")


def test_meta_and_non_string():
    f = ArbFile.from_dict({"k": "v", "@k": {"description": "d"}, "n": 5})
    assert f.meta("k") == {"description": "d"}
    assert f.non_string_keys() == ["n"]


@pytest.mark.parametrize(
    "name,loc",
    [
        ("intl_kk.arb", "kk"),
        ("app_en_US.arb", "en_US"),
        ("intl_kk_fixed.arb", "kk"),
        ("my_app_uz.arb", "uz"),
        ("strings.arb", None),
    ],
)
def test_locale_from_filename(name, loc):
    assert locale_from_filename(name) == loc


def test_declared_locale_wins():
    assert ArbFile.from_dict({"@@locale": "kk"}, "intl_ru.arb").locale == "kk"
