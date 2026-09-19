"""Which tour stops run, exercised through node like the other browser helpers."""

import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "static" / "airdate-tour.js"


def run_tour(expression: str):
    script = f"""
const tour = require({json.dumps(str(HELPER))});
process.stdout.write(JSON.stringify({expression}));
"""
    completed = subprocess.run(["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def stops_without(*missing):
    return run_tour(f"tour.availableStops((stop) => !{json.dumps(list(missing))}.includes(stop.key)).map((stop) => stop.key)")


class TourStopTests(unittest.TestCase):
    ALL = ["essays", "card", "lifecycle", "calendar", "filing", "filters", "editor", "shelf", "settings"]

    def test_the_nine_stops_in_spec_order(self):
        self.assertEqual(stops_without(), self.ALL)

    def test_every_stop_has_a_short_callout(self):
        for stop in run_tour("tour.STOPS"):
            self.assertTrue(stop["anchor"] and stop["title"], stop)
            sentences = [part for part in stop["text"].replace("?", ".").split(". ") if part.strip()]
            self.assertLessEqual(len(sentences), 3, stop["key"])

    def test_stops_without_an_element_on_screen_are_skipped(self):
        # Folders off: nothing to file. No publish day: no calendar.
        self.assertEqual(stops_without("filing", "calendar"), [k for k in self.ALL if k not in ("filing", "calendar")])
        # An empty catalog has no card to point at.
        self.assertEqual(stops_without("card", "lifecycle", "editor", "filing"), ["essays", "calendar", "filters", "shelf", "settings"])

    def test_state_is_browser_only(self):
        self.assertEqual(run_tour("tour.TOUR_KEY"), "airdate.tour")
        # Without a browser there is no storage and nothing throws.
        self.assertEqual(run_tour("[tour.tourState(), tour.start === undefined]"), ["", False])


if __name__ == "__main__":
    unittest.main()
