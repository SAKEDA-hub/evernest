"""Optional LLM reviewer for issues heuristics can't judge well (calques, unnatural word
order, subtle typos). Off by default; never edits the ARB — it only adds findings that a
human then reviews. Token usage and cost are recorded for the report.

Configuration via environment:
  LOCQA_LLM_API_KEY       required
  LOCQA_LLM_MODEL         required (model id of your provider)
  LOCQA_LLM_BASE_URL      optional (default per provider)
  LOCQA_LLM_PRICE_IN      USD per 1M input tokens  (for the cost line in the report)
  LOCQA_LLM_PRICE_OUT     USD per 1M output tokens
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import icu
from .context import Context
from .model import ISSUE_TYPES, SEVERITY_RANK, Finding

LLM_TYPES = ("machine_translation_suspect", "orthography", "wrong_language", "glossary_violation")

SYSTEM = """Ты — редактор локализации мобильной ролевой игры с открытым миром (работа, фракции, семьи, бизнес, машины).
Проверяешь перевод строк интерфейса с языка «{src}» на язык «{tgt}».
Сообщай только о реальных ошибках следующих типов:
- machine_translation_suspect: калька, неестественный порядок слов, грамматика исходного языка;
- orthography: опечатки, буквы чужого алфавита (например русские вместо казахских ә ғ қ ң ө ұ ү һ і);
- wrong_language: текст не на целевом языке;
- glossary_violation: термин переведён не по глоссарию.
Хорошие строки не трогай и не переводи заново. Плейсхолдеры в фигурных скобках ({{name}}) и ICU-конструкции сохраняй без изменений.
Ответь ТОЛЬКО JSON-массивом: [{{"key": "...", "issue_type": "...", "severity": "high|medium|low", "evidence": "коротко по-русски, почему это ошибка", "suggestion": "исправленная строка целиком или null", "confidence": 0.0}}].
Если ошибок нет — ответь []."""


def http_post(url: str, headers: dict, body: dict, timeout: float = 120) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (URL comes from config)
        return json.loads(r.read().decode())


@dataclass
class LLMClient:
    provider: str  # anthropic | openai (any OpenAI-compatible endpoint)
    model: str
    api_key: str
    base_url: str | None = None
    price_in: float = 0.0
    price_out: float = 0.0
    cache_path: str | None = ".locqa_llm_cache.json"
    transport: object = http_post
    stats: dict = field(default_factory=lambda: {"calls": 0, "cache_hits": 0, "input_tokens": 0, "output_tokens": 0})

    @classmethod
    def from_env(cls, provider: str, cache_path: str | None = ".locqa_llm_cache.json") -> "LLMClient":
        key, model = os.environ.get("LOCQA_LLM_API_KEY"), os.environ.get("LOCQA_LLM_MODEL")
        if not key or not model:
            raise RuntimeError("для --llm нужны переменные окружения LOCQA_LLM_API_KEY и LOCQA_LLM_MODEL")
        return cls(
            provider,
            model,
            key,
            os.environ.get("LOCQA_LLM_BASE_URL"),
            float(os.environ.get("LOCQA_LLM_PRICE_IN", "0") or 0),
            float(os.environ.get("LOCQA_LLM_PRICE_OUT", "0") or 0),
            cache_path,
        )

    def __post_init__(self) -> None:
        self._cache: dict = {}
        if self.cache_path and Path(self.cache_path).exists():
            try:
                self._cache = json.loads(Path(self.cache_path).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._cache = {}

    @property
    def cost_usd(self) -> float:
        return (self.stats["input_tokens"] * self.price_in + self.stats["output_tokens"] * self.price_out) / 1e6

    def complete(self, system: str, user: str) -> str:
        ck = hashlib.sha256(f"{self.provider}|{self.model}|{system}|{user}".encode()).hexdigest()
        if ck in self._cache:
            self.stats["cache_hits"] += 1
            return self._cache[ck]
        if self.provider == "anthropic":
            url = (self.base_url or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
            headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
            body = {"model": self.model, "max_tokens": 4096, "system": system, "messages": [{"role": "user", "content": user}]}
            resp = self.transport(url, headers, body)
            text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
            usage = resp.get("usage", {})
            tin, tout = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
        else:
            url = (self.base_url or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
            headers = {"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"}
            body = {
                "model": self.model,
                "temperature": 0,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            }
            resp = self.transport(url, headers, body)
            text = resp["choices"][0]["message"]["content"]
            usage = resp.get("usage", {})
            tin, tout = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
        self.stats["calls"] += 1
        self.stats["input_tokens"] += tin
        self.stats["output_tokens"] += tout
        self._cache[ck] = text
        if self.cache_path:
            Path(self.cache_path).write_text(json.dumps(self._cache, ensure_ascii=False), encoding="utf-8")
        return text


def select_keys(ctx: Context, findings: list[Finding], scope: str, limit: int | None) -> list[str]:
    if scope == "all":
        keys = [k for k, _, _ in ctx.pairs()]
    else:
        flagged = {f.key for f in findings if f.issue_type not in ("untranslated", "placeholder_mismatch")}
        keys = [k for k, _, _ in ctx.pairs() if k in flagged]
    return keys[:limit] if limit else keys


def _items(ctx: Context, keys: list[str]) -> list[dict]:
    en = ctx.refs.get("en", {})
    out = []
    for k in keys:
        item = {"key": k, "source": ctx.src[k], "target": ctx.tgt[k]}
        if ctx.description(k):
            item["description"] = ctx.description(k)
        if k in en:
            item["en"] = en[k]
        out.append(item)
    return out


def _glossary_block(ctx: Context) -> str:
    lines = [f"- {e.ru} → {e.kk}" + (f" ({e.note})" if e.note else "") for e in ctx.glossary.entries if e.kk]
    return ("Глоссарий:\n" + "\n".join(lines[:80]) + "\n\n") if lines else ""


def estimate(ctx: Context, keys: list[str], batch_size: int = 20) -> dict:
    """Rough pre-flight estimate (~2.5 chars per token for Cyrillic-heavy JSON)."""
    chars = len(SYSTEM) * max(1, -(-len(keys) // batch_size))
    chars += sum(len(json.dumps(i, ensure_ascii=False)) for i in _items(ctx, keys))
    tin = int(chars / 2.5)
    return {"keys": len(keys), "batches": -(-len(keys) // batch_size), "approx_input_tokens": tin, "approx_output_tokens": int(tin * 0.3)}


def parse_response(text: str) -> list[dict]:
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    return [d for d in data if isinstance(d, dict)]


def llm_review(ctx: Context, client: LLMClient, keys: list[str], batch_size: int = 20) -> list[Finding]:
    system = SYSTEM.format(src=ctx.source_locale, tgt=ctx.profile.name or ctx.target_locale)
    out: list[Finding] = []
    for i in range(0, len(keys), batch_size):
        batch = keys[i : i + batch_size]
        user = _glossary_block(ctx) + "Строки:\n" + json.dumps(_items(ctx, batch), ensure_ascii=False, indent=1)
        try:
            raw = client.complete(system, user)
        except Exception as e:  # network/provider errors must not kill the deterministic report
            ctx.diag("warning", "llm_error", f"батч {i // batch_size + 1}: {e}")
            continue
        for d in parse_response(raw):
            key, itype = d.get("key"), d.get("issue_type")
            if key not in batch or itype not in LLM_TYPES or itype not in ISSUE_TYPES:
                continue
            sev = d.get("severity") if d.get("severity") in SEVERITY_RANK else "medium"
            sugg = d.get("suggestion") or None
            ev = str(d.get("evidence") or "").strip() or "замечание LLM"
            if sugg:
                s_nodes, _ = icu.try_parse(ctx.src[key], ctx.options.escaping)
                n_nodes, err = icu.try_parse(sugg, ctx.options.escaping)
                if err or (s_nodes is not None and icu.arg_names(s_nodes) != icu.arg_names(n_nodes)):
                    sugg = None
                    ev += " (предложение LLM отброшено: ломает плейсхолдеры)"
            try:
                conf = min(float(d.get("confidence", 0.6)), 0.8)  # LLM self-confidence is capped
            except (TypeError, ValueError):
                conf = 0.6
            out.append(Finding(key, itype, sev, ctx.src[key], ctx.tgt[key], "LLM: " + ev, sugg, conf, rule="llm", tags=["llm"]))
    return out
