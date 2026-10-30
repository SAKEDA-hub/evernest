"""glossary_violation, inconsistent_term, and term mining for building the glossary."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from ..context import Context
from ..model import Finding
from ..text import norm, ru_stem, tokens, word_script

RU_STOP = frozenset(
    """
    и в во на не для что это с со по от из к ко у о об за как но или вы ваш ваша ваше ваши вас вам
    мы он она они его её ее их при до без уже ещё еще если чтобы только нет да все всё этот эта
    эти был была было будет можно нельзя который которые можете может нужно необходимо сейчас
    здесь более менее очень также этого этом свой своего вашего вашей вашу своей свои своих
    после перед через между когда тогда потом снова пожалуйста
    """.split()
)
KK_STOP = frozenset(
    """
    және үшін мен бен пен бұл осы сіз сіздің сізде сізге немесе да де та те ма ме ба бе па пе
    бір еді болды болады керек мүмкін сол ол оның оны осы бар жоқ әр барлық тағы енді
    """.split()
)


def check_glossary(ctx: Context):
    for key, src, tgt in ctx.pairs():
        for e in ctx.glossary.entries:
            if not e.kk:
                continue
            hit = e.match_source(src)
            if not hit:
                continue
            forb = e.forbidden_in(tgt)
            if forb is None and e.match_target(tgt):
                continue
            draft = e.status == "draft"
            allowed = " / ".join([e.kk, *e.kk_variants])
            if forb:
                ev = f"«{hit}» по глоссарию → «{allowed}», в переводе нежелательный вариант «{forb}»"
                sugg = e.fix_forbidden(tgt)
                conf, sev = 0.85, "high" if e.is_brand else "medium"
            else:
                ev = f"в исходнике термин «{hit}», в переводе нет «{allowed}»"
                sugg = None
                conf, sev = (0.85, "high") if e.is_brand else (0.65, "medium")
            if draft:
                conf -= 0.1
                ev += " (запись глоссария в статусе draft)"
            if len(tokens(src)) > 12 and not e.is_brand:
                conf = min(conf, 0.45)  # long sentences legitimately rephrase or drop a term
                ev += "; длинная фраза — термин мог быть передан описательно"
            if e.note:
                ev += f". Глоссарий: {e.note}"
            yield Finding(key, "glossary_violation", sev, src, tgt, ev, sugg, conf, rule="glossary")


# --- statistical term consistency ----------------------------------------------------


def _ru_terms(text: str) -> set[str]:
    return {
        ru_stem(w)
        for w in tokens(text)
        if word_script(w) == "cyr" and len(w) >= 5 and w.casefold() not in RU_STOP
    }


def _tgt_terms(text: str) -> dict[str, str]:
    """stem(5 chars) -> first surface form."""
    out: dict[str, str] = {}
    for w in tokens(text):
        lw = w.casefold()
        if len(lw) < 4 or lw in KK_STOP or word_script(w) not in ("cyr", "lat"):
            continue
        out.setdefault(lw[:5], lw)
    return out


@dataclass
class TermCandidate:
    ru_stem: str
    ru_example: str
    tgt_stem: str
    tgt_example: str
    support: int
    covered: int
    keys: list[str]
    outliers: list[str]

    @property
    def coverage(self) -> float:
        return self.covered / self.support if self.support else 0.0


def mine_terms(ctx: Context, min_support: int = 4, min_coverage: float = 0.75) -> list[TermCandidate]:
    """For each frequent Russian content stem find the target stem that co-occurs with it
    in most keys. High-coverage pairs are de-facto terms; keys lacking the dominant
    rendering are inconsistency candidates."""
    ru_index: dict[str, set[str]] = defaultdict(set)
    ru_surface: dict[str, Counter] = defaultdict(Counter)
    tgt_terms: dict[str, dict[str, str]] = {}
    tgt_df: Counter = Counter()
    for key, src, tgt in ctx.pairs():
        if norm(src) == norm(tgt):
            continue
        s_plain = ctx.plain("src", key)
        for w in tokens(s_plain):
            if word_script(w) == "cyr" and len(w) >= 5 and w.casefold() not in RU_STOP:
                st = ru_stem(w)
                ru_index[st].add(key)
                ru_surface[st][w.casefold()] += 1
        tt = _tgt_terms(ctx.plain("tgt", key))
        tgt_terms[key] = tt
        tgt_df.update(tt.keys())

    out: list[TermCandidate] = []
    for rs, keys in ru_index.items():
        n = len(keys)
        if n < min_support:
            continue
        co: Counter = Counter()
        surface: dict[str, Counter] = defaultdict(Counter)
        for k in keys:
            for ts, form in tgt_terms.get(k, {}).items():
                co[ts] += 1
                surface[ts][form] += 1
        best = None
        for ts, c in co.most_common(5):
            if c / n < min_coverage:
                break
            if c / max(tgt_df[ts], 1) < 0.3:  # target stem too generic
                continue
            best = (ts, c)
            break
        if not best:
            continue
        ts, c = best
        outliers = sorted(k for k in keys if ts not in tgt_terms.get(k, {}))
        out.append(
            TermCandidate(
                rs,
                ru_surface[rs].most_common(1)[0][0],
                ts,
                surface[ts].most_common(1)[0][0],
                n,
                c,
                sorted(keys),
                outliers,
            )
        )
    out.sort(key=lambda t: (-t.support, t.ru_stem))
    return out


# verb / adjective endings: such words are not glossary terms
RU_NON_TERM = re.compile(r"(ть|ться|йте|ите|ешь|ый|ий|ой|ая|ое|ые|ого)$")


def check_inconsistent(ctx: Context):
    """Strict on purpose: synonyms and paraphrases are normal in UI text, so only
    near-unanimous renderings of noun-like terms count, and only short strings are flagged."""
    glossary_stems = {ru_stem(w) for e in ctx.glossary.entries for w in tokens(e.ru)}
    for cand in mine_terms(ctx, min_support=6, min_coverage=0.85):
        if cand.coverage >= 1.0 or cand.ru_stem in glossary_stems:
            continue
        if RU_NON_TERM.search(cand.ru_example) or len(cand.outliers) > 2:
            continue  # verbs/adjectives/adverbs, or a genuinely split usage
        examples = [k for k in cand.keys if k not in cand.outliers][:3]
        for key in cand.outliers:
            src, tgt = ctx.src.get(key), ctx.tgt.get(key)
            if tgt is None or norm(src) == norm(tgt) or not ctx.looks_native(tgt):
                continue
            if cand.tgt_stem in tgt.casefold():
                continue  # present inside a compound/hyphenated word ("іс-әрекеттер")
            if len(tokens(ctx.plain("src", key))) > 8:
                continue  # long sentences are paraphrased: the term may be expressed otherwise
            conf = 0.2 + 0.5 * cand.coverage
            yield Finding(
                key,
                "inconsistent_term",
                "medium",
                src,
                tgt,
                (
                    f"«{cand.ru_example}» в {cand.covered} из {cand.support} ключей переведено как "
                    f"«{cand.tgt_example}…» (напр. {', '.join(examples)}); здесь этой формы нет"
                ),
                None,
                conf,
                rule="term_consistency",
            )
