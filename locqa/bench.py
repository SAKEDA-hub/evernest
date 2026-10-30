"""Self-evaluation without the organisers' answer key: seed synthetic errors into a target
ARB, run `check`, measure recall per issue type.

Caveat (also in README): synthetic seeds follow OUR idea of each error type, so this
recall is an optimistic proxy for recall on the hidden seeds, not an estimate of it.
"""
from __future__ import annotations

import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

from . import icu
from .arb import ArbFile
from .checks.length import ui_kind
from .glossary import Glossary
from .model import Finding
from .text import KK_SPECIFIC, norm, tokens, word_script

DELETE = object()
TO_RU = {"ә": "а", "ғ": "г", "қ": "к", "ң": "н", "ө": "о", "ұ": "у", "ү": "у", "һ": "х", "і": "и"}
HOMO = {"а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x"}


def _op_untranslated(rng, key, src, tgt, env):
    return DELETE


def _op_same(rng, key, src, tgt, env):
    if sum(1 for w in tokens(src) if word_script(w) == "cyr") >= 2 and norm(src) != norm(tgt):
        return src
    return None


def _op_placeholder(rng, key, src, tgt, env):
    names = re.findall(r"\{([A-Za-z_]\w*)\}", tgt)
    if not names or re.search(r",\s*(plural|select)", tgt):  # inside plural branches a name may legitimately repeat
        return None
    n = rng.choice(names)
    return tgt.replace("{" + n + "}", "", 1).replace("  ", " ").strip()


def _op_glossary(rng, key, src, tgt, env):
    for e in env["glossary"].entries:
        if e.is_brand or not e.kk or not e.match_source(src):
            continue
        m = e.find_target(tgt)
        if m:
            wrong = e.kk_forbidden[0] if e.kk_forbidden else e.ru.lower()
            if norm(wrong) == norm(m.group(0)) or e.match_target(wrong):
                continue  # term is spelled the same in both languages: nothing to break
            return tgt[: m.start()] + wrong + tgt[m.end() :]
    return None


def _op_length(rng, key, src, tgt, env):
    if ui_kind(key)[0] == "button" and len(tgt) <= 25:
        return tgt + " батырмасын басыңыз"
    return None


def _op_orthography(rng, key, src, tgt, env):
    words = [w for w in tokens(tgt) if any(c in KK_SPECIFIC for c in w) and "{" not in w]
    if not words:
        return None
    w = rng.choice(words)
    if rng.random() < 0.5:
        i = next(i for i, c in enumerate(w) if c in KK_SPECIFIC)
        c = w[i]
        rep = TO_RU.get(c.lower(), c)
        new_w = w[:i] + (rep.upper() if c.isupper() else rep) + w[i + 1 :]
    else:
        idx = [i for i, c in enumerate(w) if c in HOMO]
        if not idx:
            return None
        i = rng.choice(idx)
        new_w = w[:i] + HOMO[w[i]] + w[i + 1 :]
    return tgt.replace(w, new_w, 1)


def _op_wrong_language(rng, key, src, tgt, env):
    for loc in ("uz", "en"):
        ref = env["refs"].get(loc, {}).get(key)
        if ref and norm(ref) != norm(tgt) and norm(ref) != norm(src):
            return ref
    return None


def _op_suffix(rng, key, src, tgt, env):
    m = re.search(r"\{([A-Za-z_]\w*)\} ", tgt)
    if not m or "plural" in tgt:
        return None
    return tgt[: m.end() - 1] + "-ға" + tgt[m.end() - 1 :]


def _op_mt(rng, key, src, tgt, env):
    if re.search(r"(?i)(?<!\w)және(?!\w)", tgt):
        return re.sub(r"(?i)(?<!\w)және(?!\w)", "и", tgt, count=1)
    return None


OPERATORS = {
    "untranslated": _op_untranslated,
    "same_as_source": _op_same,
    "placeholder_mismatch": _op_placeholder,
    "glossary_violation": _op_glossary,
    "length_overflow": _op_length,
    "orthography": _op_orthography,
    "wrong_language": _op_wrong_language,
    "suffix_harmony": _op_suffix,
    "machine_translation_suspect": _op_mt,
}  # inconsistent_term and plural_mismatch are not seeded (no safe synthetic recipe / no plurals in data)


def seed(
    source: ArbFile,
    target: ArbFile,
    n: int = 100,
    glossary: Glossary | None = None,
    refs: dict[str, dict[str, str]] | None = None,
    rng_seed: int = 13,
) -> tuple[ArbFile, list[dict]]:
    rng = random.Random(rng_seed)
    env = {"glossary": glossary or Glossary([]), "refs": refs or {}}
    src, tgt = source.messages(), target.messages()
    keys = [k for k in src if src[k].strip() and k in tgt and tgt[k].strip() and icu.try_parse(tgt[k])[1] is None]
    rng.shuffle(keys)
    data = json.loads(json.dumps(target.data, ensure_ascii=False))
    answers: list[dict] = []
    used: set[str] = set()
    ops = list(OPERATORS.items())
    per_op = max(1, n // len(ops))
    counts: Counter = Counter()
    for _round in range(3):  # later rounds fill quota left by operators with few candidates
        for name, op in ops:
            for k in keys:
                if len(answers) >= n or counts[name] >= per_op * (_round + 1):
                    break
                if k in used:
                    continue
                new = op(rng, k, src[k], tgt[k], env)
                if new is None or (new is not DELETE and new == tgt[k]):
                    continue
                used.add(k)
                counts[name] += 1
                if new is DELETE:
                    data.pop(k, None)
                    data.pop("@" + k, None)
                else:
                    data[k] = new
                answers.append({"key": k, "issue_type": name, "original": tgt[k], "seeded": None if new is DELETE else new})
    return ArbFile(data, None, [], target.indent), answers


def evaluate(findings: list[Finding], answers: list[dict]) -> dict:
    found_exact = {(f.key, f.issue_type) for f in findings}
    found_keys = {f.key for f in findings}
    seeded_keys = {a["key"] for a in answers}
    per: dict[str, dict] = defaultdict(lambda: {"seeded": 0, "found_type": 0, "found_key": 0})
    for a in answers:
        p = per[a["issue_type"]]
        p["seeded"] += 1
        p["found_type"] += (a["key"], a["issue_type"]) in found_exact
        p["found_key"] += a["key"] in found_keys
    total = len(answers) or 1
    for p in per.values():
        p["recall_type"] = round(p["found_type"] / p["seeded"], 3)
        p["recall_key"] = round(p["found_key"] / p["seeded"], 3)
    return {
        "seeded": len(answers),
        "recall_type": round(sum(p["found_type"] for p in per.values()) / total, 3),
        "recall_key": round(sum(p["found_key"] for p in per.values()) / total, 3),
        "findings_total": len(findings),
        "findings_on_unseeded_keys": sum(1 for f in findings if f.key not in seeded_keys),
        "per_type": dict(sorted(per.items())),
    }


def save_answers(answers: list[dict], path: str | Path) -> None:
    Path(path).write_text(json.dumps(answers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_answers(path: str | Path) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
