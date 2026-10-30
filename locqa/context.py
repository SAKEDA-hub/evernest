"""Shared state for all checks: parsed files, caches, corpus statistics."""
from __future__ import annotations

import bisect
import re
from collections import Counter
from dataclasses import dataclass, field
from functools import cached_property

from . import icu
from .arb import ArbFile
from .glossary import Glossary
from .langs import base_locale, get_profile
from .model import Diagnostic
from .text import KK_SPECIFIC, char_script, tokens

# Words that are spelled identically in Kazakh and Russian in game UI (loans).
# Identical single-word strings made of these are not reported as same_as_source.
KK_LOANS = frozenset(
    """
    такси банк бизнес полиция сервер аккаунт логин пароль чат фракция фура мут варн админ
    администратор модератор бонус статус лимит рейтинг топ промокод клан лобби казино телефон
    интернет метро трамвай автобус троллейбус видео фото профиль ник депозит кредит старт финиш
    спорт футбол бокс гараж офис магазин кафе ресторан бар клуб отель такси инвентарь сайт
    форум сервис сигнал радио музыка карта код пин вип бензин маршрут сим фитнес ломбард
    автосалон салон тюнинг дрифт трек фермер ферма шахта завод порт аэропорт вокзал паспорт
    стоп пауза лидер капитан минус плюс процент баланс уровень
    """.split()
)


@dataclass
class Options:
    min_confidence: float = 0.5
    escaping: bool = False
    translit: bool = False
    allow_tokens: set[str] = field(default_factory=set)
    vocab: set[str] = field(default_factory=set)  # external target-language word list


class Context:
    def __init__(
        self,
        source: ArbFile,
        target: ArbFile,
        glossary: Glossary | None = None,
        refs: dict[str, dict[str, str]] | None = None,
        options: Options | None = None,
        source_locale: str | None = None,
        target_locale: str | None = None,
    ):
        self.source = source
        self.target = target
        self.glossary = glossary or Glossary([])
        self.refs = refs or {}
        self.options = options or Options()
        self.src = source.messages()
        self.tgt = target.messages()
        self.source_locale = source_locale or source.locale or "ru"
        self.target_locale = target_locale or target.locale or "und"
        self.profile = get_profile(self.target_locale)
        self.lang = self.profile.code
        self.source_lang = base_locale(self.source_locale)
        self.same_language = self.source_lang == self.lang
        self.diagnostics: list[Diagnostic] = []
        self._parsed: dict[tuple[str, str], tuple[list | None, str | None]] = {}

    # --- parsing -------------------------------------------------------------------
    def parse(self, which: str, key: str) -> tuple[list | None, str | None]:
        ck = (which, key)
        if ck not in self._parsed:
            text = (self.src if which == "src" else self.tgt).get(key)
            self._parsed[ck] = (None, "нет строки") if text is None else icu.try_parse(text, self.options.escaping)
        return self._parsed[ck]

    def plain(self, which: str, key: str, arg_repr: str = "") -> str:
        nodes, err = self.parse(which, key)
        if nodes is not None:
            return icu.plain(nodes, arg_repr)
        # unparsable ICU: drop brace groups so placeholder names don't leak into text checks
        text = (self.src if which == "src" else self.tgt).get(key, "")
        return re.sub(r"\{[^{}]*\}", arg_repr, text)

    def pairs(self):
        """(key, source, target) for keys translated in both files."""
        for key, s in self.src.items():
            t = self.tgt.get(key)
            if s and s.strip() and t is not None and t.strip():
                yield key, s, t

    def description(self, key: str) -> str:
        d = self.source.meta(key).get("description") or self.target.meta(key).get("description")
        return d if isinstance(d, str) else ""

    def diag(self, level: str, code: str, message: str, key: str | None = None) -> None:
        self.diagnostics.append(Diagnostic(level, code, message, key))

    # --- corpus statistics -----------------------------------------------------------
    @cached_property
    def target_script(self) -> str | None:
        c: Counter = Counter()
        for v in self.tgt.values():
            for ch in v:
                s = char_script(ch)
                if s in ("cyr", "lat"):
                    c[s] += 1
        if not c:
            return self.profile.script
        return c.most_common(1)[0][0]

    @cached_property
    def token_freq(self) -> Counter:
        c: Counter = Counter()
        for key in self.tgt:
            c.update(t.casefold() for t in tokens(self.plain("tgt", key)))
        return c

    @cached_property
    def prefix_freq(self) -> Counter:
        """Frequency of 4-letter word beginnings: Kazakh inflects by suffixes, so «қате»,
        «қатесі», «қатені» all support the spelling «қате…»."""
        c: Counter = Counter()
        for t, n in self.token_freq.items():
            if len(t) >= 4:
                c[t[:4]] += n
        return c

    @cached_property
    def _sorted_tokens(self) -> tuple[list[str], list[int]]:
        items = sorted(self.token_freq.items())
        cum, total = [0], 0
        for _, n in items:
            total += n
            cum.append(total)
        return [t for t, _ in items], cum

    def _prefix_count(self, pre: str) -> int:
        words, cum = self._sorted_tokens
        lo = bisect.bisect_left(words, pre)
        hi = bisect.bisect_left(words, pre + "\U0010ffff")
        return cum[hi] - cum[lo]

    def word_evidence(self, word: str, family: bool = False, prefix_len: int = 4) -> int:
        """How well a spelling is attested: exact corpus frequency, or with family=True the
        frequency of all words starting with `word[:prefix_len]`; plus an external dictionary."""
        w = word.casefold()
        if family and len(w) >= 4:
            if prefix_len == 4:
                n = self.prefix_freq.get(w[:4], 0)
            else:
                n = self._prefix_count(w[:prefix_len])
        else:
            n = self.token_freq.get(w, 0)
        vocab = 10 if (w in self.options.vocab or (family and any(v.startswith(w) for v in self.options.vocab))) else 0
        return n + vocab

    @cached_property
    def source_tokens(self) -> set[str]:
        out: set[str] = set()
        for key in self.src:
            out.update(t.casefold() for t in tokens(self.plain("src", key)))
        return out

    @cached_property
    def native_vocab(self) -> set[str]:
        """Frequent target words that never occur in the source: evidence a string is
        written in the target language even without language-specific letters."""
        return {t for t, n in self.token_freq.items() if n >= 3 and t not in self.source_tokens and len(t) >= 3}

    def looks_native(self, text: str) -> bool:
        if self.lang == "kk" and any(ch in KK_SPECIFIC for ch in text):
            return True
        return any(t.casefold() in self.native_vocab for t in tokens(text))

    @cached_property
    def keep_tokens(self) -> set[str]:
        out = set(self.glossary.keep_tokens()) | {t.casefold() for t in self.options.allow_tokens}
        if self.lang == "kk":
            out |= KK_LOANS
        return out
