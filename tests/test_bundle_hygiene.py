"""Nothing in this repository may assume who the writer is.

Fails on personal names, publications, machine paths or retired product names
anywhere in the shipped files. A writer's own values belong in their
config.json, which is never committed.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()

BANNED = [
    (re.compile(r"seanjosephs", re.I), "a personal account name"),
    (re.compile(r"andheresmysecret|here'?s my secret|\bahms\b", re.I), "a personal publication"),
    (re.compile(r"airdate-private", re.I), "the private repository"),
    (re.compile(r"\b8790\b"), "a personal tool's port"),
    (re.compile(r"what[-_ ]?if|subhub", re.I), "a retired product name"),
    (re.compile(r"/Users/(?!you/)[A-Za-z]"), "a machine-specific path"),
]
SKIP_DIRS = {".git", ".venv-substack", ".airdate-data", "__pycache__", "drafts"}
TEXT_SUFFIXES = {".py", ".js", ".html", ".css", ".json", ".md", ".txt", ".svg", ".command", ".sh", ""}


def shipped_files() -> list[Path]:
    try:
        listed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        paths = [ROOT / line for line in listed]
    except (OSError, subprocess.CalledProcessError):
        paths = [p for p in ROOT.rglob("*") if p.is_file()]
    return [
        p for p in paths
        if p.is_file()
        and p.resolve() != SELF
        and not any(part in SKIP_DIRS for part in p.relative_to(ROOT).parts)
        and p.suffix.lower() in TEXT_SUFFIXES
    ]


class BundleHygieneTests(unittest.TestCase):
    def test_no_personal_residue(self):
        problems = []
        for path in shipped_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for number, line in enumerate(text.splitlines(), 1):
                for pattern, why in BANNED:
                    if pattern.search(line):
                        problems.append(f"{path.relative_to(ROOT)}:{number}: {why}: {line.strip()[:100]}")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
