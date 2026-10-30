"""ARB I/O: order-preserving load, duplicate-key detection, stable write."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .langs import locale_from_filename


class ArbError(Exception):
    pass


def _detect_indent(text: str) -> int | str:
    for line in text.splitlines()[1:]:
        stripped = line.lstrip(" \t")
        if stripped and len(stripped) != len(line):
            ws = line[: len(line) - len(stripped)]
            return "\t" if "\t" in ws else len(ws)
    return 2


@dataclass
class ArbFile:
    data: dict
    path: str | None = None
    duplicates: list[str] = field(default_factory=list)
    indent: int | str = 2

    @classmethod
    def load(cls, path: str | Path) -> "ArbFile":
        p = Path(path)
        try:
            raw = p.read_bytes()
        except OSError as e:
            raise ArbError(f"не удалось прочитать {path}: {e}") from e
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise ArbError(f"{path}: файл не в UTF-8 ({e})") from e
        return cls.loads(text, path=str(path))

    @classmethod
    def loads(cls, text: str, path: str | None = None) -> "ArbFile":
        dups_per_object: list[list[str]] = []

        def hook(pairs):
            obj: dict = {}
            dups: list[str] = []
            for k, v in pairs:
                if k in obj:
                    dups.append(k)
                obj[k] = v
            dups_per_object.append(dups)
            return obj

        try:
            data = json.loads(text, object_pairs_hook=hook)
        except json.JSONDecodeError as e:
            raise ArbError(f"{path or '<arb>'}: невалидный JSON: {e}") from e
        if not isinstance(data, dict):
            raise ArbError(f"{path or '<arb>'}: корень ARB должен быть JSON-объектом")
        # the root object is decoded last: only its duplicates are duplicate ARB keys
        f = cls(data, path, dups_per_object[-1] if dups_per_object else [], _detect_indent(text))
        f.text = text
        return f

    text: str | None = None  # original source, kept for format-preserving edits

    def occurrences(self, key: str) -> list:
        """All raw values of a (possibly duplicated) top-level key, in file order."""
        if self.text is None:
            return [self.data[key]] if key in self.data else []
        return [json.loads(self.text[m.v_start : m.v_end]) for m in scan_members(self.text) if m.key == key]

    @classmethod
    def from_dict(cls, data: dict, path: str | None = None) -> "ArbFile":
        return cls(dict(data), path)

    @property
    def locale(self) -> str | None:
        loc = self.data.get("@@locale")
        if isinstance(loc, str) and loc.strip():
            return loc.strip()
        if self.path:
            return locale_from_filename(Path(self.path).name)
        return None

    def messages(self) -> dict[str, str]:
        return {k: v for k, v in self.data.items() if not k.startswith("@") and isinstance(v, str)}

    def non_string_keys(self) -> list[str]:
        return [k for k, v in self.data.items() if not k.startswith("@") and not isinstance(v, str)]

    def meta(self, key: str) -> dict:
        m = self.data.get("@" + key)
        return m if isinstance(m, dict) else {}

    def dumps(self) -> str:
        return json.dumps(self.data, ensure_ascii=False, indent=self.indent) + "\n"

    def patched(self, values: dict[str, str], meta: dict[str, dict]) -> str:
        """Source text with `values` replaced and `meta` merged into @key, everything else
        (order, comments-as-keys, duplicated @@comment markers, spacing) left byte-identical."""
        if self.text is None:
            raise ArbError("исходный текст файла недоступен")
        return patch_text(self.text, values, meta)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.dumps(), encoding="utf-8")


# --- format-preserving editing ------------------------------------------------------


@dataclass
class Member:
    key: str
    line_start: int  # start of the line holding the key (for indentation)
    k_start: int
    v_start: int
    v_end: int


_WS = " \t\r\n"


def _skip_ws(t: str, i: int) -> int:
    while i < len(t) and t[i] in _WS:
        i += 1
    return i


def _string_end(t: str, i: int) -> int:
    """t[i] == '"'; returns index after the closing quote."""
    i += 1
    while i < len(t):
        c = t[i]
        if c == "\\":
            i += 2
            continue
        if c == '"':
            return i + 1
        i += 1
    raise ArbError("незакрытая строка")


def _value_end(t: str, i: int) -> int:
    c = t[i]
    if c == '"':
        return _string_end(t, i)
    if c in "{[":
        depth = 0
        while i < len(t):
            c = t[i]
            if c == '"':
                i = _string_end(t, i)
                continue
            if c in "{[":
                depth += 1
            elif c in "}]":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        raise ArbError("незакрытый объект")
    j = i
    while j < len(t) and t[j] not in ",}] \t\r\n":
        j += 1
    return j


def scan_members(t: str) -> list[Member]:
    i = _skip_ws(t, 0)
    if i >= len(t) or t[i] != "{":
        raise ArbError("ожидался объект")
    i += 1
    out: list[Member] = []
    while True:
        i = _skip_ws(t, i)
        if t[i] == "}":
            return out
        if t[i] == ",":
            i += 1
            continue
        ks = i
        ke = _string_end(t, i)
        key = json.loads(t[ks:ke])
        i = _skip_ws(t, ke)
        if t[i] != ":":
            raise ArbError(f"ожидалось «:» после ключа {key}")
        vs = _skip_ws(t, i + 1)
        ve = _value_end(t, vs)
        out.append(Member(key, t.rfind("\n", 0, ks) + 1, ks, vs, ve))
        i = ve


def _dump(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def patch_text(t: str, values: dict[str, str], meta: dict[str, dict]) -> str:
    members = scan_members(t)
    edits: list[tuple[int, int, str]] = []  # (start, end, replacement)
    last: dict[str, Member] = {}
    for m in members:
        last[m.key] = m
    for m in members:
        if m.key in values:  # every occurrence of a duplicated key gets the same value
            edits.append((m.v_start, m.v_end, _dump(values[m.key])))
    for key, upd in meta.items():
        mk = "@" + key
        if mk in last:
            m = last[mk]
            cur = json.loads(t[m.v_start : m.v_end])
            cur = cur if isinstance(cur, dict) else {}
            cur.update(upd)
            edits.append((m.v_start, m.v_end, _dump(cur)))
        elif key in last:
            m = last[key]
            indent = t[m.line_start : m.k_start]
            indent = indent if indent.strip() == "" else "  "
            edits.append((m.v_end, m.v_end, f",\n{indent}{_dump(mk)}: {_dump(upd)}"))
    new_keys = [k for k in values if k not in last]
    if new_keys:
        anchor = members[-1].v_end if members else t.index("{") + 1
        add = "".join(
            f",\n  {_dump(k)}: {_dump(values[k])}" + (f",\n  {_dump('@' + k)}: {_dump(meta[k])}" if k in meta else "")
            for k in new_keys
        )
        if not members:
            add = add[1:]
        edits.append((anchor, anchor, add))
    for start, end, rep in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        t = t[:start] + rep + t[end:]
    return t
