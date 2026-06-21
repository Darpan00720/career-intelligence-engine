"""Lightweight observability dashboard — a single self-contained HTML page.

Served at GET /dashboard. Data is embedded server-side (read-only via
core.pipeline_metrics), so it works locally with no auth header and no JS deps.
"""
from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> str:
    from core import pipeline_metrics
    try:
        data = pipeline_metrics.dashboard_data()
    except Exception as exc:  # never 500 the dashboard
        data = {"error": str(exc)}
    return _PAGE.replace("__DATA__", json.dumps(data))


_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Career Intelligence — Pipeline Dashboard</title>
<style>
:root{--bg:#0f1420;--card:#1a2030;--ink:#e8edf5;--mut:#8a96ad;--ok:#36c08a;--accent:#4f8cff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;padding:24px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--mut);margin:24px 0 10px}
.sub{color:var(--mut);margin:0 0 20px}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
.card{background:var(--card);border-radius:10px;padding:14px}
.kpi .n{font-size:26px;font-weight:600}.kpi .l{color:var(--mut);font-size:12px}
.bar{height:22px;background:#222b3d;border-radius:5px;overflow:hidden;margin:3px 0}
.bar>span{display:block;height:100%;background:var(--accent)}
.row{display:flex;justify-content:space-between;align-items:center;gap:10px;margin:6px 0}
.row .lab{width:150px;color:var(--mut)}.row .barwrap{flex:1}.row .val{width:90px;text-align:right;font-variant-numeric:tabular-nums}
table{width:100%;border-collapse:collapse}td,th{padding:6px 8px;text-align:left;border-bottom:1px solid #222b3d}
th{color:var(--mut);font-weight:500}.ok{color:var(--ok)}
.cols{display:grid;gap:18px;grid-template-columns:1fr 1fr}@media(max-width:780px){.cols{grid-template-columns:1fr}}
</style></head><body>
<h1>Career Intelligence — Pipeline Dashboard</h1>
<p class="sub" id="sub"></p>
<h2>Run Summary</h2><div class="grid" id="kpis"></div>
<div class="cols">
  <div><h2>Pipeline Funnel</h2><div class="card" id="funnel"></div></div>
  <div><h2>Score Distribution</h2><div class="card" id="dist"></div>
       <h2>Scoring</h2><div class="grid" id="scoreKpis"></div></div>
</div>
<div class="cols">
  <div><h2>Source Performance</h2><div class="card"><table id="sources"></table></div></div>
  <div><h2>Top Companies by Score</h2><div class="card"><table id="top"></table></div></div>
</div>
<h2>Taxonomy &amp; Tracker</h2><div class="cols">
  <div class="card"><table id="taxonomy"></table></div>
  <div class="card"><table id="tracker"></table></div></div>
<script>
const D = __DATA__;
const $=id=>document.getElementById(id);
const kpi=(l,n)=>`<div class="card kpi"><div class="n">${n}</div><div class="l">${l}</div></div>`;
function bars(el,rows,max){el.innerHTML=rows.map(r=>{
  const pct=max?Math.round(100*r.v/max):0;
  return `<div class="row"><div class="lab">${r.k}</div><div class="barwrap"><div class="bar"><span style="width:${pct}%"></span></div></div><div class="val">${r.v}${r.x||''}</div></div>`;}).join('');}
function tbl(el,head,rows){el.innerHTML=`<tr>${head.map(h=>`<th>${h}</th>`).join('')}</tr>`+
  rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('');}

if(D.error){document.body.innerHTML='<h1>Dashboard error</h1><pre>'+D.error+'</pre>';}
else{
 const lr=D.last_run||{}, f=D.funnel||[], sc=D.scoring||{};
 const get=n=>(f.find(s=>s.stage===n)||{}).count||0;
 $('sub').textContent='Last run: '+(lr.run_id||'—')+'  ·  '+(sc.jobs_scored||0)+' jobs scored';
 $('kpis').innerHTML=[['Fetched',get('Fetched')],['Prefilter Kept',get('Prefilter Kept')],
   ['Scored',get('Scored')],['Research Eligible',get('Research Eligible')],
   ['Documents',get('Documents')],['Applications',get('Applications Added')]]
   .map(([l,n])=>kpi(l,n)).join('');
 // funnel
 const fmax=f.length?f[0].count||1:1;
 bars($('funnel'),f.map(s=>({k:s.stage,v:s.count,x:'  ('+s.pct_of_fetched+'%)'})),fmax);
 // distribution
 const dist=sc.distribution||{}; const dmax=Math.max(1,...Object.values(dist));
 bars($('dist'),Object.entries(dist).map(([k,v])=>({k,v})),dmax);
 $('scoreKpis').innerHTML=[['Average',sc.average_score],['p50',sc.p50],['p90',sc.p90],
   ['≥70',sc.jobs_score_ge_70],['≥80',sc.jobs_score_ge_80]].map(([l,n])=>kpi(l,n)).join('');
 // sources, top, taxonomy, tracker
 tbl($('sources'),['Source','Jobs'],Object.entries(D.source_performance||{}));
 tbl($('top'),['Company','Score'],(sc.top_companies_by_score||[]).map(c=>[c.company,c.score]));
 const tx=D.taxonomy||{}; tbl($('taxonomy'),['Category','Count'],
   Object.entries(tx.category_distribution||{}).sort((a,b)=>b[1]-a[1]));
 tbl($('tracker'),['Status','Count'],Object.entries(D.tracker||{}));
}
</script></body></html>"""
