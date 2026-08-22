import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from projectdock import search


def proj(name, path=None, pinned=False, recent_rank=None):
    return {
        "name": name,
        "path": path or f"/home/user/Projects/{name.lower()}",
        "pinned": pinned,
        "recent_rank": recent_rank,
    }


class SearchTest(unittest.TestCase):
    def test_empty_query(self):
        self.assertEqual(search.score("", proj("Foo")), 0.0)

    def test_exact_name(self):
        s = search.score("ProjectDock", proj("ProjectDock"))
        self.assertGreater(s, 90)

    def test_prefix(self):
        s = search.score("proj", proj("ProjectDock"))
        self.assertGreater(s, 60)

    def test_case_insensitive(self):
        self.assertEqual(search.score("FOO", proj("foo")), 100.0)

    def test_substring_in_path(self):
        s = search.score("cineprint", proj("Gallery", "/home/user/Projects/cine-print-gallery"))
        self.assertIsNotNone(s)

    def test_fuzzy_subsequence(self):
        s = search.score("cpn", proj("cineprint"))
        self.assertIsNotNone(s)
        self.assertGreater(s, 0)

    def test_non_match_is_none(self):
        self.assertIsNone(search.score("zzzz", proj("foo")))

    def test_multi_token_all_must_match(self):
        self.assertIsNone(search.score("cine xyz", proj("cineprint")))
        self.assertIsNotNone(search.score("cine print", proj("cineprint")))

    def test_prefix_beats_substring_beats_fuzzy(self):
        projects = [
            proj("apple"),
            proj("pineapple", "/x/pineapple"),
            proj("grape-ale", "/x/grape-ale"),
        ]
        ranked = search.filter_and_rank("ap", projects)
        self.assertEqual(ranked[0]["name"], "apple")

    def test_pinned_boost(self):
        plain = proj("beta")
        pinned = proj("beta", "/x/beta-pinned", pinned=True)
        ranked = search.filter_and_rank("beta", [plain, pinned])
        self.assertEqual(ranked[0]["path"], pinned["path"])

    def test_recent_boost(self):
        plain = proj("beta", "/x/beta-plain")
        recent = proj("beta", "/x/beta-recent", recent_rank=0)
        ranked = search.filter_and_rank("beta", [plain, recent])
        self.assertEqual(ranked[0]["path"], recent["path"])

    def test_rank_stability(self):
        projects = [proj(f"p{i}") for i in range(50)]
        first = search.filter_and_rank("p", projects)
        second = search.filter_and_rank("p", projects)
        self.assertEqual([p["name"] for p in first], [p["name"] for p in second])

    def test_sorted_by_activity(self):
        projects = [
            proj("zeta"),
            proj("alpha", recent_rank=1),
            proj("beta", pinned=True),
        ]
        ordered = search.sorted_by_activity(projects)
        self.assertEqual([p["name"] for p in ordered], ["beta", "alpha", "zeta"])


if __name__ == "__main__":
    unittest.main()
