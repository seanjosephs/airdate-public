"""The catalog's state filter, run through node like the deep-link helper."""

import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "static" / "airdate-filters.js"


def run_filters(expression: str):
    script = f"""
const filters = require({json.dumps(str(HELPER))});
process.stdout.write(JSON.stringify({expression}));
"""
    completed = subprocess.run(["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def essay(**fields):
    base = {"status": "Writers Room", "needs_intake": False, "source_role": "standalone",
            "publish_readiness": {"status": "ready", "ready_except_image": False}}
    base.update(fields)
    return base


class StateFilterTests(unittest.TestCase):
    def test_filter_is_the_lifecycle_plus_needs_attention_and_archived(self):
        self.assertEqual(run_filters("filters.STATE_FILTER_VALUES"),
                         ["all", "writers-room", "writers-likey", "ready-for-air", "live",
                          "needs-attention", "archived"])


class ArchivedIsReachableTests(unittest.TestCase):
    """Archiving used to be one-way from the catalog's point of view: the note
    moved to Archive/, left the default API scope, and no filter value could ask
    for it back. The server always supported ?scope=archived."""

    def test_archived_is_an_offered_state(self):
        self.assertIn("archived", run_filters("filters.STATE_FILTER_VALUES"))

    def test_archived_matches_only_archived_essays(self):
        self.assertTrue(run_filters(
            "filters.matchesState(%s, 'archived')" % json.dumps(essay(status="Archived"))))
        self.assertFalse(run_filters(
            "filters.matchesState(%s, 'archived')" % json.dumps(essay(status="Live"))))

    def test_archived_needs_its_own_api_scope(self):
        # Archived essays are not in the loaded catalog, so the filter has to
        # tell the caller to go and fetch them.
        self.assertEqual(run_filters("filters.scopeForState('archived')"), "archived")

    def test_lifecycle_states_need_no_extra_fetch(self):
        for value in ("all", "live", "writers-room", "needs-attention"):
            self.assertEqual(run_filters(f"filters.scopeForState({json.dumps(value)})"), "",
                             f"{value} should filter the list already loaded")

    def test_an_archived_essay_is_not_swept_into_the_lifecycle_states(self):
        archived = json.dumps(essay(status="Archived"))
        for value in ("writers-room", "writers-likey", "ready-for-air", "live"):
            self.assertFalse(run_filters(f"filters.matchesState({archived}, {json.dumps(value)})"))

    def test_needs_attention(self):
        cases = {
            "fine": (essay(), False),
            "needs filing": (essay(needs_intake=True), True),
            "missing metadata": (essay(publish_readiness={"status": "metadata"}), True),
            "missing hero image": (essay(publish_readiness={"status": "image", "ready_except_image": True}), True),
            "long source with gaps": (essay(source_role="source", publish_readiness={"status": "metadata"}), False),
            "long source needing filing": (essay(source_role="source", needs_intake=True), True),
            "no readiness yet": (essay(publish_readiness=None), False),
        }
        for name, (item, expected) in cases.items():
            self.assertEqual(run_filters(f"filters.needsAttention({json.dumps(item)})"), expected, name)

    def test_stage_and_fallback_matching(self):
        likey = essay(status="Writers Likey")
        self.assertTrue(run_filters(f"filters.matchesState({json.dumps(likey)}, 'writers-likey')"))
        self.assertFalse(run_filters(f"filters.matchesState({json.dumps(likey)}, 'live')"))
        self.assertTrue(run_filters(f"filters.matchesState({json.dumps(likey)}, 'all')"))
        # A value from the older, longer list never hides the catalog.
        self.assertTrue(run_filters(f"filters.matchesState({json.dumps(likey)}, 'planted')"))


if __name__ == "__main__":
    unittest.main()
