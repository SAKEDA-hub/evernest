"""Minimal ICU MessageFormat parser, sufficient for Flutter gen-l10n messages.

Supports simple args {name}, formatted args {n, number, ...}, plural / selectordinal /
select with nested messages and `offset:`. Apostrophe escaping is OFF by default because
Flutter gen-l10n has `use-escaping: false` by default (apostrophes are literal).
Text nodes keep their raw spans so callers can rewrite only literal text (translit).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
SELECTOR = re.compile(r"=?[A-Za-z0-9_]+")
DIGITS = re.compile(r"-?\d+")
PLURAL_KINDS = ("plural", "selectordinal")
BRANCH_KINDS = ("plural", "selectordinal", "select")


class IcuError(ValueError):
    def __init__(self, msg: str, pos: int):
        super().__init__(f"{msg} (позиция {pos})")
        self.pos = pos


@dataclass
class Text:
    value: str
    start: int
    end: int


@dataclass
class Arg:
    name: str
    kind: str  # simple | plural | selectordinal | select | number | date | time | ...
    start: int
    end: int
    branches: dict[str, list] = field(default_factory=dict)
    style: str | None = None


class _Parser:
    def __init__(self, s: str, escaping: bool):
        self.s = s
        self.i = 0
        self.esc = escaping

    def _peek(self) -> str:
        return self.s[self.i] if self.i < len(self.s) else ""

    def _ws(self) -> None:
        while self.i < len(self.s) and self.s[self.i].isspace():
            self.i += 1

    def parse(self) -> list:
        nodes = self._message(0, False)
        if self.i < len(self.s):  # only reachable on a stray '}'
            raise IcuError("непарная «}»", self.i)
        return nodes

    def _message(self, depth: int, in_plural: bool) -> list:
        s = self.s
        nodes: list = []
        buf: list[str] = []
        tstart = self.i
        while self.i < len(s):
            c = s[self.i]
            if c == "{":
                if buf:
                    nodes.append(Text("".join(buf), tstart, self.i))
                    buf = []
                nodes.append(self._arg(depth))
                tstart = self.i
            elif c == "}":
                if depth == 0:
                    raise IcuError("непарная «}»", self.i)
                break
            elif c == "'" and self.esc:
                nxt = s[self.i + 1] if self.i + 1 < len(s) else ""
                if nxt == "'":
                    buf.append("'")
                    self.i += 2
                elif nxt in ("{", "}") or (in_plural and nxt == "#"):
                    j = self.i + 1
                    closed = False
                    while j < len(s):
                        if s[j] == "'":
                            if j + 1 < len(s) and s[j + 1] == "'":
                                buf.append("'")
                                j += 2
                                continue
                            closed = True
                            break
                        buf.append(s[j])
                        j += 1
                    if not closed:
                        raise IcuError("незакрытая кавычка", self.i)
                    self.i = j + 1
                else:
                    buf.append("'")
                    self.i += 1
            else:
                buf.append(c)
                self.i += 1
        if buf:
            nodes.append(Text("".join(buf), tstart, self.i))
        return nodes

    def _arg(self, depth: int) -> Arg:
        s = self.s
        start = self.i
        self.i += 1
        self._ws()
        m = IDENT.match(s, self.i)
        if not m:
            raise IcuError("недопустимое имя плейсхолдера", self.i)
        name = m.group(0)
        self.i = m.end()
        self._ws()
        if self._peek() == "}":
            self.i += 1
            return Arg(name, "simple", start, self.i)
        if self._peek() != ",":
            raise IcuError(f"ожидалась «,» или «}}» после {{{name}", self.i)
        self.i += 1
        self._ws()
        m = IDENT.match(s, self.i)
        if not m:
            raise IcuError("ожидался тип аргумента", self.i)
        kind = m.group(0)
        self.i = m.end()
        self._ws()
        if kind in BRANCH_KINDS:
            if self._peek() != ",":
                raise IcuError(f"ожидалась «,» после {kind}", self.i)
            self.i += 1
            branches: dict[str, list] = {}
            while True:
                self._ws()
                c = self._peek()
                if c == "}":
                    self.i += 1
                    break
                if c == "":
                    raise IcuError("незакрытый аргумент", start)
                if kind != "select" and s.startswith("offset:", self.i):
                    self.i += len("offset:")
                    self._ws()
                    dm = DIGITS.match(s, self.i)
                    if not dm:
                        raise IcuError("ожидалось число после offset:", self.i)
                    self.i = dm.end()
                    continue
                sm = SELECTOR.match(s, self.i)
                if not sm:
                    raise IcuError("ожидался селектор ветки", self.i)
                sel = sm.group(0)
                self.i = sm.end()
                self._ws()
                if self._peek() != "{":
                    raise IcuError(f"ожидалась «{{» после селектора {sel}", self.i)
                self.i += 1
                sub = self._message(depth + 1, kind in PLURAL_KINDS)
                if self._peek() != "}":
                    raise IcuError("незакрытая ветка", self.i)
                self.i += 1
                if sel in branches:
                    raise IcuError(f"повторный селектор {sel}", self.i)
                branches[sel] = sub
            if not branches:
                raise IcuError("нет ни одной ветки", start)
            return Arg(name, kind, start, self.i, branches)

        style = None
        if self._peek() == ",":
            self.i += 1
            j = self.i
            nest = 0
            while self.i < len(s):
                c = s[self.i]
                if c == "{":
                    nest += 1
                elif c == "}":
                    if nest == 0:
                        break
                    nest -= 1
                self.i += 1
            style = s[j : self.i].strip()
        if self._peek() != "}":
            raise IcuError("незакрытый аргумент", start)
        self.i += 1
        return Arg(name, kind, start, self.i, {}, style)


def parse(s: str, escaping: bool = False) -> list:
    return _Parser(s, escaping).parse()


def try_parse(s: str, escaping: bool = False) -> tuple[list | None, str | None]:
    try:
        return parse(s, escaping), None
    except IcuError as e:
        return None, str(e)


def iter_args(nodes: list):
    for n in nodes:
        if isinstance(n, Arg):
            yield n
            for b in n.branches.values():
                yield from iter_args(b)


def iter_text(nodes: list):
    for n in nodes:
        if isinstance(n, Text):
            yield n
        else:
            for b in n.branches.values():
                yield from iter_text(b)


def arg_names(nodes: list) -> set[str]:
    return {a.name for a in iter_args(nodes)}


def plural_args(nodes: list) -> list[Arg]:
    return [a for a in iter_args(nodes) if a.kind in BRANCH_KINDS]


def plain(nodes: list, arg_repr: str = "") -> str:
    """Literal text as a user would see it; plural/select collapse to their `other` branch.
    `arg_repr` replaces each simple/formatted arg (use e.g. 'XXXXXX' to approximate width)."""
    out = []
    for n in nodes:
        if isinstance(n, Text):
            out.append(n.value)
        elif n.branches:
            branch = n.branches.get("other") or next(iter(n.branches.values()))
            out.append(plain(branch, arg_repr))
        else:
            out.append(arg_repr)
    return "".join(out)


def transform_text(s: str, fn, escaping: bool = False) -> str:
    """Apply `fn` to literal text spans only; placeholders, selectors and keywords untouched."""
    nodes = parse(s, escaping)
    spans = sorted((t.start, t.end) for t in iter_text(nodes))
    out, pos = [], 0
    for a, b in spans:
        out.append(s[pos:a])
        out.append(fn(s[a:b]))
        pos = b
    out.append(s[pos:])
    return "".join(out)
