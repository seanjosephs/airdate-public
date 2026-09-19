import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "static" / "airdate-deep-link.js"
APP = ROOT / "static" / "airdate.js"
HTML = ROOT / "airdate.html"


def run_resolver(search, essays):
    script = f"""
const deepLink = require({json.dumps(str(HELPER))});
const result = deepLink.resolveEssayDeepLink(
  {json.dumps(search)},
  {json.dumps(essays)}
);
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def run_handler(search, essays, reject_open=False):
    script = f"""
const deepLink = require({json.dumps(str(HELPER))});
const calls = [];
(async () => {{
  const result = await deepLink.handleEssayDeepLink(
    {json.dumps(search)},
    {json.dumps(essays)},
    {{
      openCatalog: () => calls.push(['catalog']),
      clearNotice: () => calls.push(['clear']),
      showNotice: (message, kind) => calls.push(['notice', message, kind]),
      openEditor: async (essayId) => {{
        calls.push(['editor', essayId]);
        if ({json.dumps(reject_open)}) throw new Error('editor unavailable');
      }},
    }},
  );
  process.stdout.write(JSON.stringify({{ result, calls }}));
}})();
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def run_catalog_loader(search, active_essays, all_essays):
    script = f"""
const deepLink = require({json.dumps(str(HELPER))});
const calls = [];
(async () => {{
  const essays = await deepLink.loadEssayDeepLinkCatalog(
    {json.dumps(search)},
    {json.dumps(active_essays)},
    async () => {{
      calls.push('all');
      return {{ essays: {json.dumps(all_essays)} }};
    }},
  );
  process.stdout.write(JSON.stringify({{ essays, calls }}));
}})();
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


class AirdateDeepLinkTests(unittest.TestCase):
    def test_no_essay_parameter_is_an_ordinary_catalog_visit(self):
        self.assertEqual(run_resolver("", [{"id": "essay-1"}]), {"kind": "none"})

    def test_blank_essay_parameter_keeps_catalog_open_with_notice(self):
        result = run_resolver("?essay=%20%20", [{"id": "essay-1"}])

        self.assertEqual(result["kind"], "blank")
        self.assertIn("durable essay ID", result["notice"])

    def test_valid_durable_id_resolves_exact_catalog_identity(self):
        essays = [
            {"id": "essay-1", "title": "One"},
            {"id": "essay-10", "title": "Ten"},
        ]

        self.assertEqual(
            run_resolver("?essay=essay-1", essays),
            {"kind": "open", "essayId": "essay-1"},
        )

    def test_uuid_durable_id_resolves_exactly(self):
        durable_id = "c5e72f4c-c829-4bbf-b7ff-58a6a2bd2dc9"
        essays = [{"id": durable_id, "title": "Durable"}]

        self.assertEqual(
            run_resolver(f"?essay={durable_id}", essays),
            {"kind": "open", "essayId": durable_id},
        )

    def test_unknown_durable_id_keeps_catalog_open_with_specific_notice(self):
        result = run_resolver("?essay=missing-id", [{"id": "essay-1"}])

        self.assertEqual(result["kind"], "invalid")
        self.assertEqual(result["essayId"], "missing-id")
        self.assertIn("missing-id", result["notice"])
        self.assertIn("catalog", result["notice"])

    def test_valid_link_opens_catalog_then_exact_editor(self):
        handled = run_handler("?essay=essay-1", [{"id": "essay-1"}])

        self.assertEqual(handled["result"], {"kind": "open", "essayId": "essay-1"})
        self.assertEqual(
            handled["calls"],
            [["catalog"], ["clear"], ["editor", "essay-1"]],
        )

    def test_invalid_link_opens_catalog_and_announces_notice(self):
        handled = run_handler("?essay=missing-id", [{"id": "essay-1"}])

        self.assertEqual(handled["result"]["kind"], "invalid")
        self.assertEqual(handled["calls"][0], ["catalog"])
        self.assertEqual(handled["calls"][1][0], "notice")
        self.assertEqual(handled["calls"][1][2], "bad")

    def test_editor_failure_becomes_specific_catalog_notice(self):
        handled = run_handler(
            "?essay=essay-1",
            [{"id": "essay-1"}],
            reject_open=True,
        )

        self.assertEqual(handled["result"]["kind"], "error")
        self.assertEqual(handled["calls"][-1][0], "notice")
        self.assertIn("editor unavailable", handled["calls"][-1][1])

    def test_ordinary_catalog_visit_does_not_load_all_collections(self):
        active = [{"id": "active-id", "collection": "active"}]

        loaded = run_catalog_loader("", active, [{"id": "shelf-id"}])

        self.assertEqual(loaded, {"essays": active, "calls": []})

    def test_deep_link_lookup_includes_shelf_and_archived_collections(self):
        all_essays = [
            {"id": "active-id", "collection": "active"},
            {"id": "shelf-id", "collection": "shelf"},
            {"id": "archive-id", "collection": "archived"},
        ]

        shelf = run_catalog_loader("?essay=shelf-id", [], all_essays)
        archived = run_catalog_loader("?essay=archive-id", [], all_essays)

        self.assertEqual(shelf, {"essays": all_essays, "calls": ["all"]})
        self.assertEqual(archived, {"essays": all_essays, "calls": ["all"]})
        self.assertEqual(
            run_resolver("?essay=shelf-id", shelf["essays"]),
            {"kind": "open", "essayId": "shelf-id"},
        )
        self.assertEqual(
            run_resolver("?essay=archive-id", archived["essays"]),
            {"kind": "open", "essayId": "archive-id"},
        )


class AirdateDeepLinkIntegrationContractTests(unittest.TestCase):
    def test_page_has_accessible_deep_link_notice(self):
        html = HTML.read_text(encoding="utf-8")

        notice_tag = next(
            line for line in html.splitlines() if 'id="deep-link-notice"' in line
        )
        self.assertIn('aria-live="polite"', notice_tag)
        self.assertIn('role="status"', notice_tag)
        self.assertNotIn(" hidden", notice_tag)

        source = APP.read_text(encoding="utf-8")
        css = (ROOT / "static" / "air-date.css").read_text(encoding="utf-8")
        self.assertNotIn("deepLinkNoticeEl.hidden", source)
        self.assertIn(".deep-link-notice:empty", css)
        empty_rule = css[css.index(".deep-link-notice:empty") :]
        empty_rule = empty_rule[: empty_rule.index("}")]
        self.assertNotIn("display: none", empty_rule)
        self.assertIn("clip-path: inset(50%)", empty_rule)

    def test_helper_loads_before_main_airdate_script(self):
        html = HTML.read_text(encoding="utf-8")

        helper_index = html.index('/static/airdate-deep-link.js')
        app_index = html.index('/static/airdate.js')
        self.assertLess(helper_index, app_index)

    def test_boot_handles_deep_link_only_after_catalog_loads(self):
        source = APP.read_text(encoding="utf-8")
        init_source = source[source.index("async function init()") :]

        load_index = init_source.index("await loadEssays();")
        deep_link_index = init_source.index("await handleInitialEssayDeepLink();")
        self.assertLess(load_index, deep_link_index)
        self.assertIn("AirdateDeepLink.handleEssayDeepLink", source)
        self.assertIn("/api/essays?scope=all", source)

    def test_editor_is_labelled_modal_dialog(self):
        html = HTML.read_text(encoding="utf-8")
        dialog_tag = next(
            line for line in html.splitlines() if 'id="editor-shell"' in line
        )

        self.assertIn('role="dialog"', dialog_tag)
        self.assertIn('aria-modal="true"', dialog_tag)
        self.assertIn('aria-labelledby="editor-title"', dialog_tag)
        self.assertIn('tabindex="-1"', dialog_tag)

    def test_editor_manages_initial_focus_escape_trap_and_restoration(self):
        source = APP.read_text(encoding="utf-8")

        self.assertIn("editorReturnFocus", source)
        self.assertIn("function closeEditor()", source)
        self.assertIn("function handleEditorDialogKeydown(event)", source)
        self.assertIn("event.key === 'Escape'", source)
        self.assertIn("event.key !== 'Tab'", source)
        self.assertIn("editorShellEl.focus({ preventScroll: true })", source)
        self.assertIn("returnFocus.focus({ preventScroll: true })", source)
        self.assertIn("document.addEventListener('keydown', handleEditorDialogKeydown)", source)


if __name__ == "__main__":
    unittest.main()
