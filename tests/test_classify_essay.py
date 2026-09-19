import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ClassifyEssayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # server.py reads its folders from the environment at import; point it
        # at an empty temp tree so the test never touches the real vault.
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["OBSIDIAN_ESSAYS_DIR"] = cls.tmp.name
        os.environ["AIR_DATE_DATA_DIR"] = str(Path(cls.tmp.name) / "runtime")
        sys.path.insert(0, str(ROOT))
        import server  # noqa: PLC0415
        cls.server = server

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def classify(self, frontmatter, relative_path="Politics & AI/Note.md", title="Note"):
        return self.server.classify_essay(relative_path, title, frontmatter)

    def test_plain_essay_is_active(self):
        self.assertEqual(self.classify({"status": "Writers Room"}), "active")

    def test_type_research_is_hidden(self):
        self.assertEqual(self.classify({"type": "research", "tags": ["essay", "ai"]}), "hidden")

    def test_research_tag_is_hidden(self):
        self.assertEqual(self.classify({"tags": ["research", "essay"]}), "hidden")

    def test_type_research_is_case_insensitive(self):
        self.assertEqual(self.classify({"type": "Research"}), "hidden")

    def test_index_type_still_hidden(self):
        self.assertEqual(self.classify({"type": "index"}), "hidden")

    def test_published_still_shelf(self):
        self.assertEqual(self.classify({"status": "Published"}), "shelf")


if __name__ == "__main__":
    unittest.main()
