"""Career graph platform tests — ontologies, versioning, paths, tenant isolation."""
import unittest

from tests._career_support import CareerDB
from core import tenancy
from core.career_graph import (
    CareerGraph,
    PREREQUISITE,
    RoleOntology,
    SkillOntology,
    slugify,
)


class TestSkillOntology(CareerDB):
    def test_add_and_get_skill(self):
        onto = SkillOntology()
        onto.add_skill("Machine Learning", category="hard")
        s = onto.get_skill("machine learning")
        self.assertEqual(s.slug, "machine-learning")
        self.assertEqual(s.category, "hard")

    def test_versioned_ontology_incremental(self):
        onto = SkillOntology()
        onto.add_skill("Python")
        self.assertEqual(onto.current_version(), 1)
        v = onto.publish_version([("Rust", "hard"), ("Go", "hard")])
        self.assertEqual(v, 2)
        self.assertEqual(onto.current_version(), 2)
        self.assertEqual(onto.get_skill("Rust").version, 2)

    def test_list_by_category(self):
        onto = SkillOntology()
        onto.add_skill("Python", category="hard")
        onto.add_skill("Leadership", category="soft")
        self.assertEqual([s.slug for s in onto.list_skills(category="soft")], ["leadership"])


class TestRoleOntology(CareerDB):
    def test_role_with_skills_and_industry(self):
        ro = RoleOntology()
        ro.add_role("AI Product Manager", industry="tech",
                    required_skills=["Python", "Product Management"])
        role = ro.get_role("ai product manager")
        self.assertEqual(role.industry, "tech")
        self.assertIn("python", role.required_skills)
        self.assertEqual(ro.industries(), ["tech"])


class TestCareerPaths(CareerDB):
    def _graph(self):
        g = CareerGraph()
        g.add_role("Analyst", required_skills=["sql"])
        g.add_role("Senior Analyst")
        g.add_role("Product Manager", required_skills=["sql", "roadmapping", "leadership"])
        g.add_transition("Analyst", "Senior Analyst", difficulty=1.0, typical_months=12)
        g.add_transition("Senior Analyst", "Product Manager", difficulty=2.0, typical_months=18)
        g.add_transition("Analyst", "Product Manager", difficulty=5.0, typical_months=24)
        return g

    def test_shortest_path_minimizes_difficulty(self):
        g = self._graph()
        path = g.plan_transition("Analyst", "Product Manager")
        # via Senior Analyst (1+2=3) beats the direct edge (5)
        self.assertEqual(path.nodes, ["analyst", "senior-analyst", "product-manager"])
        self.assertEqual(path.total_difficulty, 3.0)
        self.assertEqual(path.total_months, 30)
        self.assertEqual(path.steps, 2)

    def test_unreachable_returns_none(self):
        g = self._graph()
        g.add_role("Astronaut")
        self.assertIsNone(g.plan_transition("Analyst", "Astronaut"))

    def test_prerequisite_chain_is_topological(self):
        g = CareerGraph()
        g.add_skill("machine learning")
        g.add_skill("python")
        g.add_skill("statistics")
        g.add_prerequisite("machine learning", "python")
        g.add_prerequisite("machine learning", "statistics")
        g.add_prerequisite("python", "programming-basics")
        chain = g.paths.prerequisite_chain("machine learning")
        # programming-basics must precede python
        self.assertLess(chain.index("programming-basics"), chain.index("python"))
        self.assertNotIn("machine-learning", chain)

    def test_skill_gap_for_role(self):
        g = self._graph()
        gaps = g.gap_to_role(["sql"], "Product Manager")
        self.assertCountEqual(gaps, ["roadmapping", "leadership"])


class TestTenantIsolation(CareerDB):
    def test_skills_isolated_per_tenant(self):
        onto = SkillOntology()
        with tenancy.use_tenant("acme"):
            onto.add_skill("Acme Secret Skill")
        with tenancy.use_tenant("beta"):
            self.assertEqual(onto.list_skills(), [])
            self.assertIsNone(onto.get_skill("Acme Secret Skill"))


class TestSlugify(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(slugify("  Machine  Learning! "), "machine-learning")
        self.assertEqual(slugify("C++"), "c")


if __name__ == "__main__":
    unittest.main(verbosity=2)
