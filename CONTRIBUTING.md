# Contributing to airdate

Thanks for looking. airdate is a small, deliberately old-fashioned tool, and
the constraints below are not accidents. Reading this first will save you from
writing a patch I have to turn down for reasons you could not have guessed.

## The stack is locked

airdate is **vanilla HTML, CSS and JavaScript on the front end, and a Python
standard-library server on the back end**. That is a permanent decision, not a
stage it will grow out of.

**Not accepted, in any patch:**

- React, Vue, Svelte, or any front-end framework
- npm, yarn, pnpm, or a `package.json`
- A bundler, transpiler or build step of any kind
- TypeScript
- Python dependencies in the server itself

The server imports only the Python standard library. The one exception in the
whole project is `.venv-substack`, an isolated virtualenv that exists purely so
`substack_draft.py` can talk to Substack, and it is created on the user's
machine at install time rather than shipped.

Why: a writer should be able to clone this, read it, and run it in five
minutes on a Mac with nothing installed but Python, and it should still work in
five years without a dependency having rotted underneath it.

## Things airdate will never do

These are product promises, enforced by tests. A patch that weakens one will
not be merged, however well written.

- **It never publishes.** airdate creates Substack drafts and stops. No
  publishing, no scheduling, no emailing subscribers, and no setting that
  changes this. `tests/test_draft_only.py` guards it by reading the three files
  that could reach Substack and failing on the obvious route in: an `Api`
  method outside a reviewed allowlist, or a call whose name looks like
  publishing, scheduling or sending. Know what it is: a tripwire on the front
  door. A patch that reached Substack some other way could pass it, so review
  is the real backstop and a patch that moves toward publishing will be turned
  down even if the suite is green.
- **It never deletes a note.**
- **It never touches anything outside the essays folder**, besides reading
  totem images the user points it at.
- **It has no AI inside it.** "Copy thumbnail prompt" puts text on your
  clipboard. That is the whole feature.
- **It never sends telemetry**, and it has no auto-update mechanism.

## Running the tests

All of them are offline. None touches the network or your real vault.

```bash
cd /path/to/airdate
python3 -m unittest discover -s tests
python3 scripts/workflow-smoke.py
python3 scripts/frontmatter-roundtrip-test.py
python3 scripts/obsidian-fidelity-test.py
```

The smoke test starts airdate against a throwaway vault and a fake connector.
CI runs the unit tests and the smoke test on Ubuntu and macOS, against Python
3.10 and 3.13, on every push and pull request including from a fork. A patch
needs all of it green.

## Before you open a pull request

**For a bug fix:** go ahead. A failing test that demonstrates the bug, plus the
fix, is the ideal shape.

**For anything that changes how airdate looks or behaves, open an issue
first.** Visual design, copy, and the lifecycle model are decided before code
here, and a surprise PR that redesigns a screen is work neither of us gets back.
This is not gatekeeping for its own sake; it is the only way a one-person
project avoids churn.

**Especially open an issue first for:** new dependencies (see above), anything
touching the connector's trust model or the bridge token, anything that widens
what the server binds to, and anything touching frontmatter keys.

## House conventions

- **airdate is always lowercase and one word**, including at the start of a
  sentence, in prose, in the interface and in commit messages. Never "AirDate",
  never "Air Date", never "air-date". The two legacy status aliases in
  `normalize_status` are stored data and stay as they are.
- Match the surrounding code. It is plain and unclever on purpose.
- Keep comments where they explain a decision that is not obvious from the
  code, which is the density the existing files already use.
- Write documentation the way SETUP.md is written: plainly, saying the
  uncomfortable part out loud rather than around it.

## Security

Do not open a public issue for anything that could expose a Substack session,
the bridge token, or a vault's contents. See [SECURITY.md](SECURITY.md) for the
private channel.

## License

airdate is MIT. By contributing, you agree your contribution is licensed the
same way.
