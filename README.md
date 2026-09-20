# airdate

[![tests](https://github.com/seanjosephs/airdate-public/actions/workflows/tests.yml/badge.svg)](https://github.com/seanjosephs/airdate-public/actions/workflows/tests.yml)

A local essay manager for writers who keep their essays in **Obsidian** and
publish on **Substack**.

![The airdate catalog: a calendar rail on the left, and essay cards showing
each essay's state, tags and lifecycle buttons](docs/catalog.png)

*The catalog, running against a demo vault.*

- Reads a folder of Markdown notes in your Obsidian vault and shows them as a
  catalog you can search, filter, and sort.
- Tracks each essay from Writers Room to Published, with a weekly calendar on
  your publish day.
- Edits an essay's Substack metadata (subtitle, email subject, SEO and social
  text, hero image) and saves it back into the note's frontmatter.
- Checks an essay before sending and refuses anything that would lose text on
  the way to Substack.
- **Sends a finished essay to Substack as a draft. It never publishes,
  schedules, or emails subscribers.** You press publish in Substack yourself.

Both Obsidian and Substack are required. Your Substack sign-in lives in a small
Obsidian plugin (the airdate connector), never in airdate.

airdate is for technical writers: setup takes a terminal, Python 3.10+, and a
few minutes. The Substack client it uses is unofficial and cookie-based.
**Read [SETUP.md](SETUP.md) before installing**; it explains exactly what that
means.

## Quick start

```bash
git clone https://github.com/seanjosephs/airdate-public.git airdate
cd airdate
scripts/setup
./run-airdate.command
```

**Clone it rather than downloading the ZIP.** A cloned folder runs as-is. A
ZIP downloaded through a browser is quarantined by macOS, and because
`run-airdate.command` is not code-signed, Gatekeeper will refuse to open it. If
you already downloaded one, [SETUP.md](SETUP.md#troubleshooting) has the
one-line fix.

Then follow [SETUP.md](SETUP.md) to point airdate at your vault and connect
Substack through Obsidian.

## What is in here

| Path | What it is |
|---|---|
| `server.py` | The local web server (Python standard library only) |
| `airdate.html`, `static/` | The interface (plain HTML, CSS, JavaScript) |
| `airdate_config.py` | Settings: `.airdate-data/config.json`, with neutral defaults |
| `substack_draft.py` | Creates one Substack draft; run only by the connector |
| `obsidian-airdate-connector/` | The Obsidian plugin that holds your Substack session |
| `scripts/setup`, `scripts/install-connector` | Install helpers |
| `tests/`, `scripts/*-test.py`, `scripts/workflow-smoke.py` | Offline tests |
| `config.example.json` | Every setting, with examples |
| `SETUP.md` | Install, first run, and what the unofficial Substack client means |
| `CONTRIBUTING.md` | The locked stack and what to open an issue about first |
| `SECURITY.md` | The trust model, and how to report a problem privately |

## Contributing

The stack is locked on purpose: no framework, no npm, no bundler, no
TypeScript, and a standard-library-only server. Bug fixes are welcome as pull
requests; anything that changes how airdate looks or behaves wants an issue
first. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Security

airdate holds a live Substack session, through Obsidian's secret storage.
[SECURITY.md](SECURITY.md) explains the trust model, the known tradeoffs, and
how to report a problem privately rather than in a public issue.

## Acknowledgements

Sending works because of [python-substack](https://github.com/ma2za/python-substack)
by [ma2za](https://github.com/ma2za), an unofficial, MIT-licensed community
client for Substack. Substack publishes no official way to create a draft, so
without that library airdate would stop at the edge of your vault. If you use
airdate, the thanks belong there too.

## License

MIT. See [LICENSE](LICENSE).

airdate installs, but does not redistribute, its Python dependencies:
python-substack (MIT), PyYAML (MIT), requests (Apache-2.0), certifi (MPL-2.0),
urllib3 (MIT), idna (BSD-3-Clause), charset-normalizer (MIT) and python-dotenv
(BSD-3-Clause). `scripts/setup` fetches them into `.venv-substack` on your own
machine, at the versions pinned in `requirements-substack.txt`.
