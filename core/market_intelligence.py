"""Market Intelligence Platform (v5.5).

Aggregates job-market signal into demand, salary, and trend intelligence with
historical, tenant-isolated snapshots.

  JobMarketCollector   — normalize raw job postings into market observations
  MarketSnapshotService— persist/read point-in-time demand snapshots (history)
  DemandAnalyzer       — demand by role / industry / geography
  SalaryIntelligence   — percentile salary benchmarks (persisted + lookup)
  TrendAnalyzer        — growth between snapshots + emerging-skills detection

Pure statistics over data you pass in (or pull from the local ``jobs`` table);
no external API calls.
"""
from __future__ import annotations

import json
import statistics
from collections import Counter
from dataclasses import dataclass, field

from core import database, tenancy
from core.career_graph import slugify


@dataclass
class MarketObservation:
    role_slug: str
    title: str
    geography: str
    industry: str = ""
    skills: list[str] = field(default_factory=list)
    salary: float | None = None


class JobMarketCollector:
    """Normalize raw job dicts into :class:`MarketObservation` records."""

    def collect(self, jobs: list[dict]) -> list[MarketObservation]:
        obs = []
        for j in jobs:
            obs.append(MarketObservation(
                role_slug=slugify(j.get("role_category") or j.get("title", "")),
                title=j.get("title", ""),
                geography=(j.get("location") or "").strip(),
                industry=j.get("industry", ""),
                skills=[slugify(s) for s in (j.get("skills") or [])],
                salary=j.get("salary")))
        return obs

    def from_db(self, *, limit: int = 1000) -> list[MarketObservation]:
        with database.get_connection() as conn:
            rows = conn.execute(
                "SELECT title, location, role_category FROM jobs LIMIT ?", (limit,)).fetchall()
        return self.collect([dict(r) for r in rows])


class DemandAnalyzer:
    def by_role(self, observations: list[MarketObservation]) -> dict[str, int]:
        return dict(Counter(o.role_slug for o in observations if o.role_slug))

    def by_geography(self, observations: list[MarketObservation]) -> dict[str, int]:
        return dict(Counter(o.geography for o in observations if o.geography))

    def by_industry(self, observations: list[MarketObservation]) -> dict[str, int]:
        return dict(Counter(o.industry for o in observations if o.industry))

    def top_roles(self, observations: list[MarketObservation], n: int = 5) -> list[tuple[str, int]]:
        return Counter(o.role_slug for o in observations if o.role_slug).most_common(n)


class SalaryIntelligence:
    def benchmark(self, role: str, geography: str, salaries: list[float], *,
                  currency: str = "EUR", persist: bool = False,
                  tenant_id: str | None = None) -> dict:
        clean = sorted(s for s in salaries if s and s > 0)
        if not clean:
            return {"role_slug": slugify(role), "geography": geography,
                    "p25": None, "p50": None, "p75": None, "sample_size": 0}
        result = {
            "role_slug": slugify(role), "geography": geography, "currency": currency,
            "p25": round(_percentile(clean, 25), 2),
            "p50": round(_percentile(clean, 50), 2),
            "p75": round(_percentile(clean, 75), 2),
            "sample_size": len(clean),
        }
        if persist:
            self._save(result, tenant_id=tenant_id)
        return result

    def _save(self, b: dict, *, tenant_id: str | None = None) -> None:
        tenant_id = tenant_id or tenancy.current_tenant()
        with database.get_connection() as conn:
            conn.execute(
                "INSERT INTO salary_benchmarks "
                "(tenant_id, role_slug, geography, currency, p25, p50, p75, sample_size) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (tenant_id, b["role_slug"], b["geography"], b.get("currency", "EUR"),
                 b["p25"], b["p50"], b["p75"], b["sample_size"]))

    def lookup(self, role: str, geography: str, *,
               tenant_id: str | None = None) -> dict | None:
        tenant_id = tenant_id or tenancy.current_tenant()
        with database.get_connection() as conn:
            row = conn.execute(
                "SELECT currency, p25, p50, p75, sample_size FROM salary_benchmarks "
                "WHERE tenant_id = ? AND role_slug = ? AND geography = ? "
                "ORDER BY captured_at DESC LIMIT 1",
                (tenant_id, slugify(role), geography)).fetchone()
        return dict(row) if row else None


class MarketSnapshotService:
    def __init__(self, demand: DemandAnalyzer | None = None):
        self.demand = demand or DemandAnalyzer()

    def capture(self, role: str, geography: str, observations: list[MarketObservation], *,
                tenant_id: str | None = None) -> int:
        tenant_id = tenant_id or tenancy.current_tenant()
        role_slug = slugify(role)
        relevant = [o for o in observations
                    if o.role_slug == role_slug and (not geography or o.geography == geography)]
        skill_counts = Counter(s for o in relevant for s in o.skills)
        data = {"skill_counts": dict(skill_counts),
                "geographies": self.demand.by_geography(relevant)}
        with database.get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO market_snapshots "
                "(tenant_id, role_slug, geography, demand_count, data) VALUES (?, ?, ?, ?, ?)",
                (tenant_id, role_slug, geography, len(relevant), json.dumps(data)))
            return cur.lastrowid

    def history(self, role: str, geography: str | None = None, *,
                tenant_id: str | None = None) -> list[dict]:
        tenant_id = tenant_id or tenancy.current_tenant()
        q = ("SELECT demand_count, data, captured_at FROM market_snapshots "
             "WHERE tenant_id = ? AND role_slug = ?")
        args: list = [tenant_id, slugify(role)]
        if geography:
            q += " AND geography = ?"
            args.append(geography)
        q += " ORDER BY id"
        with database.get_connection() as conn:
            rows = conn.execute(q, args).fetchall()
        return [{"demand_count": r["demand_count"], "data": json.loads(r["data"] or "{}"),
                 "captured_at": r["captured_at"]} for r in rows]


class TrendAnalyzer:
    def demand_growth(self, history: list[dict]) -> float:
        """Relative change in demand from first to last snapshot."""
        if len(history) < 2:
            return 0.0
        first, last = history[0]["demand_count"], history[-1]["demand_count"]
        if first == 0:
            return float(last)
        return round((last - first) / first, 4)

    def emerging_skills(self, history: list[dict], *, min_growth: float = 1.0) -> list[str]:
        """Skills whose mention count grew by at least ``min_growth`` (relative)
        between the first and last snapshot."""
        if len(history) < 2:
            return []
        first = Counter(history[0]["data"].get("skill_counts", {}))
        last = Counter(history[-1]["data"].get("skill_counts", {}))
        emerging = []
        for skill, last_count in last.items():
            base = first.get(skill, 0)
            growth = float(last_count) if base == 0 else (last_count - base) / base
            if growth >= min_growth:
                emerging.append(skill)
        return sorted(emerging)


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)
