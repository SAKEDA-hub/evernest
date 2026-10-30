"""seed/eval, LLM client (fake transport), transliteration."""
import json

from conftest import make_ctx

from locqa import engine
from locqa.arb import ArbFile
from locqa.bench import evaluate, seed
from locqa.context import Context
from locqa.llm import LLMClient, estimate, llm_review, parse_response
from locqa.translit import translit_arb, translit_kk


def test_seed_then_detect():
    ctx0 = make_ctx(refs=True)
    seeded, answers = seed(ctx0.source, ctx0.target, n=30, glossary=ctx0.glossary, refs=ctx0.refs, rng_seed=1)
    assert len(answers) >= 15
    assert len({a["key"] for a in answers}) == len(answers)
    ctx = Context(ctx0.source, ArbFile(seeded.data, "intl_kk.arb"), ctx0.glossary, ctx0.refs)
    res = evaluate(engine.run(ctx), answers)
    for t in ("untranslated", "placeholder_mismatch", "suffix_harmony", "wrong_language"):
        if t in res["per_type"]:
            assert res["per_type"][t]["recall_type"] == 1.0, (t, res["per_type"][t])
    assert res["recall_key"] >= 0.7


def test_llm_review_with_fake_transport(tmp_path):
    ctx = make_ctx({"quest_new": "Жаңа тапсырма жасалған болып табылады"})
    calls = []

    def fake(url, headers, body):
        calls.append((url, body))
        text = json.dumps(
            [
                {"key": "quest_new", "issue_type": "machine_translation_suspect", "severity": "medium",
                 "evidence": "калька", "suggestion": "Жаңа тапсырма", "confidence": 0.99},
                {"key": "quest_new", "issue_type": "orthography", "suggestion": "{bad}"},
                {"key": "not_in_batch", "issue_type": "orthography"},
                {"key": "quest_new", "issue_type": "made_up_type"},
            ],
            ensure_ascii=False,
        )  # fmt: skip
        return {"content": [{"type": "text", "text": "Ответ:\n" + text}], "usage": {"input_tokens": 1000, "output_tokens": 200}}

    client = LLMClient("anthropic", "test-model", "k", price_in=3.0, price_out=15.0, cache_path=str(tmp_path / "c.json"), transport=fake)
    out = llm_review(ctx, client, ["quest_new"])
    assert calls[0][0].endswith("/v1/messages")
    by = {f.issue_type: f for f in out}
    assert set(by) == {"machine_translation_suspect", "orthography"}
    assert by["machine_translation_suspect"].confidence == 0.8  # capped
    assert by["orthography"].suggestion is None and "ломает плейсхолдеры" in by["orthography"].evidence
    assert round(client.cost_usd, 6) == round((1000 * 3 + 200 * 15) / 1e6, 6)
    # second run is served from cache: no new HTTP call, no new cost
    client2 = LLMClient("anthropic", "test-model", "k", cache_path=str(tmp_path / "c.json"), transport=fake)
    llm_review(ctx, client2, ["quest_new"])
    assert len(calls) == 1 and client2.stats["cache_hits"] == 1
    assert estimate(ctx, ["quest_new"])["batches"] == 1


def test_llm_transport_error_is_diagnostic(tmp_path):
    ctx = make_ctx()

    def boom(url, headers, body):
        raise TimeoutError("timeout")

    client = LLMClient("openai", "m", "k", cache_path=None, transport=boom)
    assert llm_review(ctx, client, ["quest_new"]) == []
    assert any(d.code == "llm_error" for d in ctx.diagnostics)


def test_parse_response_tolerates_garbage():
    assert parse_response("no json here") == []
    assert parse_response("[{bad json") == []
    assert parse_response('```json\n[{"key": "a"}]\n```') == [{"key": "a"}]


def test_translit_keeps_icu_intact():
    assert translit_kk("Қате") == "Qate"
    arb = ArbFile.from_dict({"@@locale": "kk", "a": "{count, plural, one{{count} ойыншы} other{{count} ойыншы}}", "@a": {}})
    out, errors = translit_arb(arb)
    assert not errors
    assert out.data["a"] == "{count, plural, one{{count} oiynşy} other{{count} oiynşy}}"
    assert out.data["@@locale"] == "kk_Latn"
