import pytest

from locqa import icu


def test_simple_and_formatted_args():
    nodes = icu.parse("Привет, {name}! У вас {n, number, compact} монет")
    assert icu.arg_names(nodes) == {"name", "n"}
    assert icu.plain(nodes, "X") == "Привет, X! У вас X монет"


def test_plural_nested_and_other_branch():
    s = "{count, plural, offset:1 =0{нет} one{{count} зат {who}} other{{count} зат}}"
    nodes = icu.parse(s)
    (p,) = icu.plural_args(nodes)
    assert p.kind == "plural" and set(p.branches) == {"=0", "one", "other"}
    assert icu.arg_names(nodes) == {"count", "who"}
    assert icu.plain(nodes, "N") == "N зат"


def test_select_inside_plural():
    s = "{g, select, male{{n, plural, one{a} other{b}}} other{c}}"
    kinds = sorted(a.kind for a in icu.plural_args(icu.parse(s)))
    assert kinds == ["plural", "select"]


@pytest.mark.parametrize(
    "bad",
    ["{name", "a}b", "{1}", "{ имя }", "{n, plural, one{x}", "{n, plural, }", "{n, plural, one{a} one{b} other{c}}", "{n plural}"],
)
def test_syntax_errors(bad):
    nodes, err = icu.try_parse(bad)
    assert nodes is None and err


def test_escaping_off_by_default_like_flutter():
    assert icu.arg_names(icu.parse("it's {name}")) == {"name"}
    # with escaping on, quoted braces are literal text
    assert icu.arg_names(icu.parse("'{name}' and {x}", escaping=True)) == {"x"}
    assert icu.plain(icu.parse("it''s", escaping=True)) == "it's"


def test_transform_text_touches_only_literals():
    s = "{count, plural, one{{count} зат} other{{count} зат}} {name}"
    out = icu.transform_text(s, str.upper)
    assert out == "{count, plural, one{{count} ЗАТ} other{{count} ЗАТ}} {name}"
