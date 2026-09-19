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
    def test_filter_is_the_lifecycle_plus_needs_attention(self):
        self.assertEqual(run_filters("filters.STATE_FILTER_VALUES"),
                         ["all", "writers-room", "writers-likey", "ready-for-air", "live", "needs-attention"])

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
