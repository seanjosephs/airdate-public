"""The draft-only promise, enforced.

airdate creates Substack drafts and stops. It cannot publish, schedule or email
subscribers. These tests read the three places that could ever reach Substack
and fail if any of them grows a way to do more than create or update a draft.
They run offline and need no Substack account.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Every python-substack Api method substack_draft.py may call. Adding one means
# deciding, in review, that it cannot publish, schedule or send email.
ALLOWED_API_METHODS = {
    "get_user_id",
    "get_image",
    "post_draft",
    "put_draft",
    "get_sections",
    "add_tags_to_post",
}

# Names that would publish, schedule or send. None may appear as a call.
FORBIDDEN_CALL = re.compile(r"(prepublish|publish_draft|unpublish|schedule|send_email|send_test)", re.IGNORECASE)


def calls_on(tree: ast.AST, receiver: str) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == receiver
        ):
            names.add(node.func.attr)
    return names


def all_call_names(tree: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
            elif isinstance(node.func, ast.Name):
                names.add(node.func.id)
    return names


class SubstackDraftCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "substack_draft.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_only_draft_api_methods_are_called(self):
        used = calls_on(self.tree, "api")
        self.assertTrue(used, "expected substack_draft.py to call the Api")
        self.assertLessEqual(used, ALLOWED_API_METHODS, f"unreviewed Api calls: {sorted(used - ALLOWED_API_METHODS)}")

    def test_no_publish_or_schedule_calls_anywhere(self):
        offending = sorted(name for name in all_call_names(self.tree) if FORBIDDEN_CALL.search(name))
        self.assertEqual(offending, [], f"publish/schedule-shaped calls: {offending}")

    def test_publish_flag_refuses(self):
        # The --publish flag exists only to fail loudly if someone wires it.
        self.assertIn('if args.publish:', self.source)
        self.assertRegex(self.source, r"if args\.publish:\s*\n\s*fail\(")


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "server.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_server_never_imports_a_substack_client_or_spawns_processes(self):
        imported = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertNotIn("substack", imported)
        self.assertNotIn("subprocess", imported, "the server must not run a transport itself; the connector does")

    def test_only_outbound_request_is_the_loopback_connector(self):
        opener_functions = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef):
                if any(
                    isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute) and inner.func.attr == "urlopen"
                    for inner in ast.walk(node)
                ):
                    opener_functions.append(node.name)
        self.assertEqual(opener_functions, ["connector_request"])
        self.assertIn('return f"http://127.0.0.1:{port}"', self.source)

    def test_connector_is_only_asked_for_status_connect_and_draft(self):
        paths = set(re.findall(r'connector_request\(\s*"(/[a-z-]+)"', self.source))
        self.assertEqual(paths, {"/status", "/connect", "/draft"})


class ConnectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "obsidian-airdate-connector" / "main.js").read_text(encoding="utf-8")

    def test_connector_runs_only_substack_draft(self):
        self.assertEqual(len(re.findall(r"\bexecFile\(", self.source)), 1, "exactly one child process")
        for other in ("spawn(", "exec(", "execSync(", "spawnSync(", "fork("):
            self.assertNotIn(other, self.source.replace("execFile(", ""))
        self.assertIn('path.join(this.settings.airdateRoot, "substack_draft.py")', self.source)

    def test_connector_makes_no_substack_requests_of_its_own(self):
        for forbidden in ("fetch(", "requestUrl(", "https.request", "http.request(", "net.request"):
            self.assertNotIn(forbidden, self.source)
        urls = set(re.findall(r"https://[^\s\"'`]+", self.source))
        self.assertEqual(urls, {"https://substack.com/sign-in"})

    def test_connector_ignores_caller_supplied_paths(self):
        self.assertNotIn("payload.python", self.source)
        self.assertNotIn("payload.airdate_root", self.source)


if __name__ == "__main__":
    unittest.main()
