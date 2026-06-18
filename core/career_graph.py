"""Career Graph Platform (v5.5).

A tenant-isolated, versioned knowledge graph over skills, roles, industries and
career transitions, backed by the additive v5.5 tables (`skills`,
`skill_relationships`, `role_definitions`, `career_paths`).

  SkillOntology      — versioned skills graph + prerequisite relationships
  RoleOntology       — versioned roles graph (required skills, industry, seniority)
  RelationshipManager— typed edges (prerequisite | related | similar) + transitions
  CareerPathEngine   — shortest career-transition path (Dijkstra over difficulty),
                       prerequisite-chain resolution, skill-gap for a target role
  CareerGraph        — facade wiring the above together

Design notes
------------
* Pure orchestration over SQLite; every read/write is scoped to the current
  tenant (``core.tenancy``) so two tenants never see each other's ontology.
* Ontologies are *versioned*: ``skills`` / ``role_definitions`` carry a
  ``version`` column and queries resolve the latest version per slug, so a new
  ontology release is an additive insert (incremental update) — old rows stay
  for reproducibility.
* No external services; swap in a graph DB behind these classes later without
  touching call sites.
"""
from __future__ import annotations

import heapq
import json
import re
from dataclasses import dataclass, field

from core import database, tenancy

_SLUG_RE = re.compile(r"[^a-z0-9]+")

PREREQUISITE = "prerequisite"
RELATED = "related"
SIMILAR = "similar"


def slugify(text: str) -> str:
    return _SLUG_RE.sub("-", (text or "").strip().lower()).strip("-")


# ── Skill ontology ────────────────────────────────────────────────────────────

@dataclass
class Skill:
    name: str
    slug: str
    category: str = "hard"
    version: int = 1


class SkillOntology:
    """Versioned skills graph. Latest version per slug wins on read."""

    def add_skill(self, name: str, *, category: str = "hard", version: int = 1,
                  tenant_id: str | None = None) -> Skill:
        tenant_id = tenant_id or tenancy.current_tenant()
        slug = slugify(name)
        with database.get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO skills (tenant_id, name, slug, category, version) "
                "VALUES (?, ?, ?, ?, ?)", (tenant_id, name, slug, category, version))
        return Skill(name, slug, category, version)

    def get_skill(self, name_or_slug: str, *, tenant_id: str | None = None) -> Skill | None:
        tenant_id = tenant_id or tenancy.current_tenant()
        slug = slugify(name_or_slug)
        with database.get_connection() as conn:
            row = conn.execute(
                "SELECT name, slug, category, MAX(version) AS version FROM skills "
                "WHERE tenant_id = ? AND slug = ? GROUP BY slug", (tenant_id, slug)).fetchone()
        return Skill(row["name"], row["slug"], row["category"], row["version"]) if row else None

    def list_skills(self, *, category: str | None = None,
                    tenant_id: str | None = None) -> list[Skill]:
        tenant_id = tenant_id or tenancy.current_tenant()
        q = ("SELECT name, slug, category, MAX(version) AS version FROM skills "
             "WHERE tenant_id = ?")
        args: list = [tenant_id]
        if category:
            q += " AND category = ?"
            args.append(category)
        q += " GROUP BY slug ORDER BY slug"
        with database.get_connection() as conn:
            rows = conn.execute(q, args).fetchall()
        return [Skill(r["name"], r["slug"], r["category"], r["version"]) for r in rows]

    def current_version(self, *, tenant_id: str | None = None) -> int:
        tenant_id = tenant_id or tenancy.current_tenant()
        with database.get_connection() as conn:
            row = conn.execute("SELECT COALESCE(MAX(version), 0) AS v FROM skills "
                               "WHERE tenant_id = ?", (tenant_id,)).fetchone()
        return int(row["v"])

    def publish_version(self, skills: list[tuple[str, str]], *,
                        tenant_id: str | None = None) -> int:
        """Publish a new ontology version (incremental update). ``skills`` is a
        list of ``(name, category)``. Returns the new version number."""
        tenant_id = tenant_id or tenancy.current_tenant()
        version = self.current_version(tenant_id=tenant_id) + 1
        for name, category in skills:
            self.add_skill(name, category=category, version=version, tenant_id=tenant_id)
        return version


# ── Role ontology ─────────────────────────────────────────────────────────────

@dataclass
class Role:
    slug: str
    title: str
    industry: str = ""
    required_skills: list[str] = field(default_factory=list)
    seniority: str = ""
    version: int = 1


class RoleOntology:
    def add_role(self, title: str, *, industry: str = "", required_skills=None,
                 seniority: str = "", version: int = 1,
                 tenant_id: str | None = None) -> Role:
        tenant_id = tenant_id or tenancy.current_tenant()
        slug = slugify(title)
        skills = [slugify(s) for s in (required_skills or [])]
        with database.get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO role_definitions "
                "(tenant_id, slug, title, industry, required_skills, seniority, version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (tenant_id, slug, title, industry, json.dumps(skills), seniority, version))
        return Role(slug, title, industry, skills, seniority, version)

    def get_role(self, title_or_slug: str, *, tenant_id: str | None = None) -> Role | None:
        tenant_id = tenant_id or tenancy.current_tenant()
        slug = slugify(title_or_slug)
        with database.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM role_definitions WHERE tenant_id = ? AND slug = ? "
                "ORDER BY version DESC LIMIT 1", (tenant_id, slug)).fetchone()
        if not row:
            return None
        return Role(row["slug"], row["title"], row["industry"],
                    json.loads(row["required_skills"] or "[]"), row["seniority"], row["version"])

    def list_roles(self, *, industry: str | None = None,
                   tenant_id: str | None = None) -> list[Role]:
        tenant_id = tenant_id or tenancy.current_tenant()
        with database.get_connection() as conn:
            rows = conn.execute(
                "SELECT slug, title, industry, required_skills, seniority, MAX(version) AS version "
                "FROM role_definitions WHERE tenant_id = ? GROUP BY slug ORDER BY slug",
                (tenant_id,)).fetchall()
        roles = [Role(r["slug"], r["title"], r["industry"],
                      json.loads(r["required_skills"] or "[]"), r["seniority"], r["version"])
                 for r in rows]
        if industry:
            roles = [r for r in roles if r.industry == industry]
        return roles

    def industries(self, *, tenant_id: str | None = None) -> list[str]:
        return sorted({r.industry for r in self.list_roles(tenant_id=tenant_id) if r.industry})


# ── Relationships (skill edges + role transitions) ─────────────────────────────

class RelationshipManager:
    def add_relationship(self, source: str, target: str, relation: str = RELATED, *,
                         weight: float = 1.0, tenant_id: str | None = None) -> None:
        tenant_id = tenant_id or tenancy.current_tenant()
        with database.get_connection() as conn:
            conn.execute(
                "INSERT INTO skill_relationships (tenant_id, source, target, relation, weight) "
                "VALUES (?, ?, ?, ?, ?)",
                (tenant_id, slugify(source), slugify(target), relation, weight))

    def neighbors(self, node: str, *, relation: str | None = None,
                  tenant_id: str | None = None) -> list[dict]:
        tenant_id = tenant_id or tenancy.current_tenant()
        q = ("SELECT target, relation, weight FROM skill_relationships "
             "WHERE tenant_id = ? AND source = ?")
        args: list = [tenant_id, slugify(node)]
        if relation:
            q += " AND relation = ?"
            args.append(relation)
        with database.get_connection() as conn:
            rows = conn.execute(q, args).fetchall()
        return [{"target": r["target"], "relation": r["relation"], "weight": r["weight"]}
                for r in rows]

    def add_transition(self, from_role: str, to_role: str, *, difficulty: float = 1.0,
                       typical_months: int = 12, tenant_id: str | None = None) -> None:
        tenant_id = tenant_id or tenancy.current_tenant()
        with database.get_connection() as conn:
            conn.execute(
                "INSERT INTO career_paths (tenant_id, from_role, to_role, difficulty, typical_months) "
                "VALUES (?, ?, ?, ?, ?)",
                (tenant_id, slugify(from_role), slugify(to_role), difficulty, typical_months))

    def transitions_from(self, role: str, *, tenant_id: str | None = None) -> list[dict]:
        tenant_id = tenant_id or tenancy.current_tenant()
        with database.get_connection() as conn:
            rows = conn.execute(
                "SELECT to_role, difficulty, typical_months FROM career_paths "
                "WHERE tenant_id = ? AND from_role = ?", (tenant_id, slugify(role))).fetchall()
        return [{"to_role": r["to_role"], "difficulty": r["difficulty"],
                 "typical_months": r["typical_months"]} for r in rows]


# ── Path engine ────────────────────────────────────────────────────────────────

@dataclass
class CareerPath:
    nodes: list[str]
    total_difficulty: float
    total_months: int

    @property
    def steps(self) -> int:
        return max(0, len(self.nodes) - 1)


class CareerPathEngine:
    def __init__(self, roles: RoleOntology, relationships: RelationshipManager,
                 skills: SkillOntology | None = None):
        self.roles = roles
        self.rel = relationships
        self.skills = skills or SkillOntology()

    def shortest_path(self, from_role: str, to_role: str, *,
                      tenant_id: str | None = None) -> CareerPath | None:
        """Dijkstra over the career-transition graph, minimizing cumulative
        difficulty. Returns ``None`` if the target is unreachable."""
        tenant_id = tenant_id or tenancy.current_tenant()
        start, goal = slugify(from_role), slugify(to_role)
        if start == goal:
            return CareerPath([start], 0.0, 0)
        # (cost, node, path, months)
        pq: list[tuple[float, str, list[str], int]] = [(0.0, start, [start], 0)]
        best: dict[str, float] = {start: 0.0}
        while pq:
            cost, node, path, months = heapq.heappop(pq)
            if node == goal:
                return CareerPath(path, round(cost, 4), months)
            if cost > best.get(node, float("inf")):
                continue
            for edge in self.rel.transitions_from(node, tenant_id=tenant_id):
                nxt = edge["to_role"]
                new_cost = cost + max(0.0, edge["difficulty"])
                if new_cost < best.get(nxt, float("inf")):
                    best[nxt] = new_cost
                    heapq.heappush(pq, (new_cost, nxt, path + [nxt],
                                        months + edge["typical_months"]))
        return None

    def prerequisite_chain(self, skill: str, *, tenant_id: str | None = None) -> list[str]:
        """Topologically ordered list of prerequisites that must precede ``skill``
        (cycle-safe; the target skill itself is excluded)."""
        tenant_id = tenant_id or tenancy.current_tenant()
        order: list[str] = []
        seen: set[str] = set()
        stack: set[str] = set()

        def visit(node: str) -> None:
            if node in seen or node in stack:
                return
            stack.add(node)
            for edge in self.rel.neighbors(node, relation=PREREQUISITE, tenant_id=tenant_id):
                visit(edge["target"])
            stack.discard(node)
            seen.add(node)
            if node != slugify(skill):
                order.append(node)

        visit(slugify(skill))
        return order

    def skill_gap_for_role(self, have_skills, target_role: str, *,
                           tenant_id: str | None = None) -> list[str]:
        role = self.roles.get_role(target_role, tenant_id=tenant_id)
        if not role:
            return []
        have = {slugify(s) for s in have_skills}
        return [s for s in role.required_skills if s not in have]


# ── Facade ─────────────────────────────────────────────────────────────────────

class CareerGraph:
    """One entry point over the whole career knowledge graph."""

    def __init__(self):
        self.skills = SkillOntology()
        self.roles = RoleOntology()
        self.relationships = RelationshipManager()
        self.paths = CareerPathEngine(self.roles, self.relationships, self.skills)

    # convenience pass-throughs commonly used by the workflow / recommender
    def add_skill(self, name, **kw):
        return self.skills.add_skill(name, **kw)

    def add_role(self, title, **kw):
        return self.roles.add_role(title, **kw)

    def add_prerequisite(self, skill, prerequisite, **kw):
        self.relationships.add_relationship(skill, prerequisite, PREREQUISITE, **kw)

    def add_transition(self, from_role, to_role, **kw):
        self.relationships.add_transition(from_role, to_role, **kw)

    def plan_transition(self, from_role, to_role, **kw) -> CareerPath | None:
        return self.paths.shortest_path(from_role, to_role, **kw)

    def gap_to_role(self, have_skills, target_role, **kw) -> list[str]:
        return self.paths.skill_gap_for_role(have_skills, target_role, **kw)
