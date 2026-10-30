"""report.json and self-contained HTML report."""
from __future__ import annotations

import html
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .context import Context
from .model import ISSUE_TYPES, Finding


def build_report(
    ctx: Context,
    findings: list[Finding],
    suppressed: list[Finding],
    min_confidence: float,
    llm_stats: dict | None = None,
) -> dict:
    return {
        "tool": "locqa",
        "version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {"path": ctx.source.path, "locale": ctx.source_locale, "keys": len(ctx.src)},
        "target": {"path": ctx.target.path, "locale": ctx.target_locale, "keys": len(ctx.tgt)},
        "min_confidence": min_confidence,
        "summary": {
            "findings": len(findings),
            "keys_with_findings": len({f.key for f in findings}),
            "suppressed_below_threshold": len(suppressed),
            "by_type": {t: n for t, n in Counter(f.issue_type for f in findings).most_common()},
            "by_severity": dict(Counter(f.severity for f in findings)),
            "reviewed_by_human": sum(1 for f in findings if f.reviewed_by_human),
        },
        "llm": llm_stats or {"enabled": False},
        "findings": [f.to_dict() for f in findings],
        "diagnostics": [d.to_dict() for d in ctx.diagnostics],
    }


def load_findings(path: str | Path) -> list[Finding]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data["findings"] if isinstance(data, dict) else data
    return [Finding.from_dict(d) for d in items]


def write_json(report: dict, path: str | Path, flat: bool = False) -> None:
    payload = report["findings"] if flat else report
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


_HTML = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>locqa — __TITLE__</title>
<style>
:root{--bg:#fbfaf7;--fg:#1d1d1b;--mute:#6b6a65;--line:#e4e1d8;--card:#fff;--hi:#b42318;--med:#b54708;--low:#475467;--ok:#067647;--acc:#1f4fd1;--mark:#fff1c2}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--fg:#ecebe6;--mute:#a09f99;--line:#2e2d2a;--card:#1c1c1a;--hi:#f97066;--med:#fdb022;--low:#98a2b3;--ok:#47cd89;--acc:#84adff;--mark:#4a3d10}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
header{padding:24px 16px 8px;max-width:1280px;margin:auto}h1{font-size:20px;margin:0 0 4px}
.meta{color:var(--mute);font-size:13px}main{max-width:1280px;margin:auto;padding:0 16px 48px}
.cards{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 12px;min-width:120px;cursor:pointer}
.card b{display:block;font-size:20px}.card.on{outline:2px solid var(--acc)}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0 12px;position:sticky;top:0;background:var(--bg);padding:8px 0;z-index:1}
input,select{font:inherit;padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg)}
input[type=search]{flex:1;min-width:200px}
.f{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--low);border-radius:8px;padding:10px 12px;margin:8px 0}
.f.high{border-left-color:var(--hi)}.f.medium{border-left-color:var(--med)}
.h{display:flex;flex-wrap:wrap;gap:8px;align-items:baseline}.k{font-family:ui-monospace,Menlo,monospace;font-weight:600}
.pill{font-size:12px;padding:1px 8px;border-radius:99px;border:1px solid var(--line);color:var(--mute)}
.sev-high{color:var(--hi)}.sev-medium{color:var(--med)}.rv{color:var(--ok)}
.grid{display:grid;grid-template-columns:110px 1fr;gap:2px 12px;margin:6px 0 0}
.grid dt{color:var(--mute)}.grid dd{margin:0;white-space:pre-wrap;word-break:break-word}
.sug{background:var(--mark);border-radius:4px;padding:0 4px}
details{margin-top:24px}table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid var(--line);padding:4px 6px;text-align:left;font-size:13px}
.empty{color:var(--mute);padding:24px 0}
</style></head><body>
<header><h1>Отчёт locqa: __TITLE__</h1><div class="meta" id="meta"></div></header>
<main>
<div class="cards" id="cards"></div>
<div class="bar">
<input type="search" id="q" placeholder="Поиск: ключ, тип, текст, evidence">
<select id="sev"><option value="">все severity</option><option>high</option><option>medium</option><option>low</option></select>
<select id="rev"><option value="">ревью: любое</option><option value="1">проверено</option><option value="0">не проверено</option></select>
<span class="meta" id="count"></span>
</div>
<div id="list"></div>
<details><summary>Диагностика файлов (<span id="dcount"></span>)</summary><table id="diag"></table></details>
</main>
<script type="application/json" id="data">__DATA__</script>
<script>
const R=JSON.parse(document.getElementById('data').textContent);
const $=id=>document.getElementById(id);let typeF='';
function el(t,c,txt){const e=document.createElement(t);if(c)e.className=c;if(txt!=null)e.textContent=txt;return e}
$('meta').textContent=`${R.source.path||'source'} (${R.source.locale}, ${R.source.keys} ключей) → ${R.target.path||'target'} (${R.target.locale}, ${R.target.keys} ключей) · порог confidence ${R.min_confidence} · скрыто ниже порога: ${R.summary.suppressed_below_threshold} · ${R.generated_at}`;
function cards(){const c=$('cards');c.textContent='';
 const all=el('div','card'+(typeF===''?' on':''));all.append(el('b',null,R.summary.findings),'все находки');all.onclick=()=>{typeF='';cards();render()};c.append(all);
 for(const [t,n] of Object.entries(R.summary.by_type)){const d=el('div','card'+(typeF===t?' on':''));d.append(el('b',null,n),t);d.onclick=()=>{typeF=t;cards();render()};c.append(d)}}
function row(dl,k,v,cls){if(v==null||v==='')return;dl.append(el('dt',null,k));const dd=el('dd');const s=el('span',cls,v);dd.append(s);dl.append(dd)}
function render(){const q=$('q').value.toLowerCase(),sv=$('sev').value,rv=$('rev').value;const L=$('list');L.textContent='';let n=0;
 for(const f of R.findings){if(typeF&&f.issue_type!==typeF)continue;if(sv&&f.severity!==sv)continue;if(rv!==''&&String(+f.reviewed_by_human)!==rv)continue;
  if(q&&![f.key,f.issue_type,f.source,f.target,f.evidence,f.suggestion].join(' ').toLowerCase().includes(q))continue;
  if(++n>2000)continue;
  const d=el('div','f '+f.severity);const h=el('div','h');h.append(el('span','k',f.key),el('span','pill',f.issue_type),el('span','pill sev-'+f.severity,f.severity),el('span','pill','conf '+f.confidence));
  if(f.reviewed_by_human)h.append(el('span','pill rv','проверено человеком'));(f.tags||[]).forEach(t=>h.append(el('span','pill',t)));d.append(h);
  const dl=el('dl','grid');row(dl,'источник',f.source);row(dl,'перевод',f.target);row(dl,'evidence',f.evidence);row(dl,'предложение',f.suggestion,'sug');d.append(dl);L.append(d)}
 $('count').textContent=`показано: ${Math.min(n,2000)} из ${n}`;if(!n)L.append(el('div','empty','Нет находок под фильтр'))}
['q','sev','rev'].forEach(i=>$(i).addEventListener('input',render));
$('dcount').textContent=R.diagnostics.length;const T=$('diag');for(const d of R.diagnostics){const tr=el('tr');tr.append(el('td',null,d.level),el('td',null,d.code),el('td',null,d.key||''),el('td',null,d.message));T.append(tr)}
cards();render();
</script></body></html>
"""


def write_html(report: dict, path: str | Path) -> None:
    title = f"{report['source']['locale']} → {report['target']['locale']}"
    # "<" only occurs inside JSON strings, so < is a valid escape and cannot close <script>
    data = json.dumps(report, ensure_ascii=False).replace("<", "\\u003c")
    out = _HTML.replace("__TITLE__", html.escape(title)).replace("__DATA__", data)
    Path(path).write_text(out, encoding="utf-8")


__all__ = ["build_report", "write_json", "write_html", "load_findings", "ISSUE_TYPES"]
