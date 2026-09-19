"""The Obsidian connector's pairing and path rules, run under node when it is
installed (skipped otherwise; Obsidian itself is not needed)."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN_JS = ROOT / "obsidian-airdate-connector" / "main.js"

SCRIPT = r"""
const fs = require("fs"), path = require("path");
const c = require(process.argv[2]);
const root = fs.realpathSync(process.argv[3]);
const outside = fs.realpathSync(process.argv[4]);
const py = path.join(root, ".venv-substack", "bin", "python");
const draft = path.join(root, "drafts", "note.md");
const out = {
  pairOk: c.validatePairing(root, "").ok,
  pairNotAirdate: c.validatePairing(outside, "").ok,
  pairRelative: c.validatePairing("relative/path", "").ok,
  pairMissingPython: c.validatePairing(root, path.join(root, "nope")).ok,
  pinnedPython: c.validatePairing(root, "").python === py,
  draftOk: c.resolveDraftFile(root, draft).ok,
  draftOutside: c.resolveDraftFile(root, path.join(root, "server.py")).ok,
  draftTraversal: c.resolveDraftFile(root, path.join(root, "drafts", "..", "server.py")).ok,
  draftSymlink: c.resolveDraftFile(root, path.join(root, "drafts", "link.md")).ok,
  draftNotMd: c.resolveDraftFile(root, path.join(root, "drafts", "note.txt")).ok,
  unpaired: c.resolveDraftFile("", draft).ok,
  ports: [c.parsePort("17778"), c.parsePort(" 17777 "), c.parsePort("80"), c.parsePort("abc"), c.parsePort("70000")],
};
const file = c.writePairingFile(path.join(root, ".airdate-data"), 17777, "t0ken");
out.pairingMode = (fs.statSync(file).mode & 0o777).toString(8);
out.pairing = JSON.parse(fs.readFileSync(file, "utf8"));
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class ConnectorPairingTests(unittest.TestCase):
    def test_pairing_and_draft_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "airdate"
            outside = Path(tmp) / "elsewhere"
            (root / "drafts").mkdir(parents=True)
            (root / ".venv-substack" / "bin").mkdir(parents=True)
            outside.mkdir()
            for name in ("server.py", "substack_draft.py"):
                (root / name).write_text("", encoding="utf-8")
            (root / ".venv-substack" / "bin" / "python").write_text("", encoding="utf-8")
            (root / "drafts" / "note.md").write_text("x", encoding="utf-8")
            (root / "drafts" / "note.txt").write_text("x", encoding="utf-8")
            (outside / "secret.md").write_text("x", encoding="utf-8")
            (root / "drafts" / "link.md").symlink_to(outside / "secret.md")
            script = Path(tmp) / "check.js"
            script.write_text(SCRIPT, encoding="utf-8")
            done = subprocess.run(["node", str(script), str(MAIN_JS), str(root), str(outside)],
                                  capture_output=True, text=True, timeout=30)
            self.assertEqual(done.returncode, 0, done.stderr)
            out = json.loads(done.stdout)
        self.assertTrue(out["pairOk"])
        self.assertTrue(out["pinnedPython"])
        for refused in ("pairNotAirdate", "pairRelative", "pairMissingPython", "draftOutside",
                        "draftTraversal", "draftSymlink", "draftNotMd", "unpaired"):
            self.assertFalse(out[refused], refused)
        self.assertTrue(out["draftOk"])
        self.assertEqual(out["ports"], [17778, 17777, 0, 0, 0])
        self.assertEqual(out["pairingMode"], "600")
        self.assertEqual(out["pairing"], {"port": 17777, "token": "t0ken"})


if __name__ == "__main__":
    unittest.main()
