# airdate

A local essay manager for writers who keep their essays in **Obsidian** and
publish on **Substack**.

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

## License

MIT. See [LICENSE](LICENSE). The Substack client airdate installs,
[python-substack](https://pypi.org/project/python-substack/), is also MIT.
