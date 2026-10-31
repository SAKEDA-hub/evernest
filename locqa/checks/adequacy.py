"""Meaning check without a dictionary: word associations learned from the file itself.

Every key is a sentence pair, so across ~5k pairs a Russian stem and its Kazakh rendering
co-occur far more often than chance (Dice coefficient). For a short string, a Kazakh word
that in the rest of the file translates something else and is unrelated to every word of
this source suggests a wrong or swapped translation (e.g. a word that elsewhere
translates a different Russian word). Statistics are computed leave-one-out so a string
cannot vouch for itself.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from functools import lru_cache

from ..context import Context
from ..model import Finding
from ..text import norm, ru_stem, tokens, word_script
from .terms import KK_STOP, RU_STOP

MIN_EVIDENCE = 3  # occurrences of a stem elsewhere needed to judge it
UNRELATED = 0.05  # Dice below this = "never translates any of these words"
EXPLAINED = 0.1  # Dice above this = the word may well translate a source word
STRONG = 0.3  # the alien word must clearly belong to some other Russian word


def _ru(text: str) -> set[str]:
    return {ru_stem(w) for w in tokens(text) if word_script(w) == "cyr" and len(w) >= 3 and w.casefold() not in RU_STOP}


def _kk(text: str) -> set[str]:
    return {
        w.casefold()[:5]
        for w in tokens(text)
        if word_script(w) == "cyr" and len(w) >= 3 and w.casefold() not in KK_STOP
    }


class Assoc:
    def __init__(self, pairs: list[tuple[set[str], set[str]]]):
        self.cr: Counter = Counter()
        self.ck: Counter = Counter()
        self.c: dict[str, Counter] = defaultdict(Counter)
        for r, k in pairs:
            self.cr.update(r)
            self.ck.update(k)
            for a in r:
                self.c[a].update(k)

    def dice(self, r: str, k: str, own: bool) -> float:
        d = 1 if own else 0  # leave-one-out: drop the current pair's own contribution
        cr, ck, c = self.cr[r] - d, self.ck[k] - d, self.c[r][k] - d
        return 2 * c / (cr + ck) if cr > 0 and ck > 0 else 0.0

    @lru_cache(maxsize=None)
    def best_source(self, k: str) -> str | None:
        best, score = None, 0.0
        for r, cnt in self.cr.items():
            if cnt < 2:
                continue
            s = self.dice(r, k, own=False)
            if s > score:
                best, score = r, s
        return best if score >= STRONG else None


def check_adequacy(ctx: Context):
    if ctx.same_language:
        return
    data = []
    for key, src, tgt in ctx.pairs():
        if norm(src) == norm(tgt):
            continue
        data.append((key, src, tgt, _ru(ctx.plain("src", key)), _kk(ctx.plain("tgt", key))))
    assoc = Assoc([(r, k) for _, _, _, r, k in data])
    surface: dict[str, Counter] = defaultdict(Counter)
    for key, src, tgt, _, _ in data:
        for w in tokens(ctx.plain("src", key)):
            surface[ru_stem(w)][w.casefold()] += 1

    for key, src, tgt, r, k in data:
        if not r or not k or len(r) > 4:
            continue  # long sentences are paraphrased too freely for word statistics
        if any(assoc.cr[rw] - 1 < MIN_EVIDENCE for rw in r):
            continue  # a rare source word could be what the Kazakh renders: no judgement
        alien = []
        explained = 0
        for kw in sorted(k):
            if assoc.ck[kw] - 1 < MIN_EVIDENCE:
                continue  # rare word: no basis for a judgement
            best_here = max(assoc.dice(rw, kw, own=True) for rw in r)
            if best_here >= EXPLAINED:
                explained += 1
            elif best_here < UNRELATED:
                other = assoc.best_source(kw)
                if other and other not in r:
                    alien.append((kw, other))
        judged = sum(1 for kw in k if assoc.ck[kw] - 1 >= MIN_EVIDENCE)
        if not alien or explained or len(alien) < judged:
            continue  # at least one word matches the source: likely a paraphrase, not a swap
        parts = [f"«{kw}…» в других строках переводит «{surface[o].most_common(1)[0][0] if surface[o] else o}»" for kw, o in alien[:3]]
        yield Finding(
            key,
            "machine_translation_suspect",
            "medium",
            src,
            tgt,
            "смысл перевода не совпадает с исходником: " + "; ".join(parts) + " и не связано ни с одним словом исходника",
            None,
            0.45,  # below the default threshold: a lead for review, too noisy for the report
            rule="adequacy_assoc",
        )
