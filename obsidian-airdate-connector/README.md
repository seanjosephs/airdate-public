# airdate connector

A small, desktop-only Obsidian plugin that lets [airdate](../README.md) send a
finished essay to Substack as a draft.

It is **not** a standalone plugin. On its own it does nothing. It is one half
of airdate, and it is installed by airdate's own script rather than from
Obsidian's community plugin directory.

## Why it exists

airdate is a local Python server. It has no browser, so it cannot sign you in
to Substack. Obsidian does have a browser, and it has encrypted secret storage.
So the connector handles the sign-in, keeps the session where a session should
live, and airdate never touches it.

The split is the point: **airdate never sees your Substack session.** It asks
the connector to make a draft, and the connector answers with the result.

## What you actually do

Nothing by hand. You never copy a cookie, paste a token, or edit a config file.

1. Run `python3 scripts/install-connector` from your airdate folder. It copies
   this plugin into `<vault>/.obsidian/plugins/airdate-connector/`.
2. In Obsidian, enable **airdate connector** under Settings, Community plugins.
3. Run **Pair with airdate** from the command palette and give it your airdate
   folder.
4. Run **Connect Substack for airdate** and sign in to Substack in the window
   that opens. Google sign-in works.

That is the whole setup. Full instructions, including what to do when
something does not connect, are in [SETUP.md](../SETUP.md#connect-substack-through-obsidian).

## Commands

| Command | What it does |
|---|---|
| **Pair with airdate** | Points the connector at your airdate folder and writes the shared bridge token. |
| **Connect Substack for airdate** | Opens a Substack sign-in window and stores the resulting session. |
| **Disconnect Substack for airdate** | Forgets the stored session. Run this before switching Substack accounts. |

## What it stores, and where

- **Your Substack session** goes into Obsidian's secret storage on this
  computer. It is never written to `data.json`, never into your vault, and
  never into git.
- **The bridge token** that airdate and the connector use to trust each other
  also lives in secret storage, plus one copy in airdate's own
  `.airdate-data/secrets/` folder, readable only by you. It is deliberately
  kept out of the vault so that syncing or committing your vault does not
  carry it.

Because neither is in `data.json`, syncing your vault to another machine does
not move your Substack sign-in with it. You sign in again there. That is
intended.

## What it will and will not run

Pairing pins the airdate folder and the Python interpreter. After that:

- It runs exactly one program, `substack_draft.py`, from the folder you paired.
- It only accepts a draft file inside that folder's `drafts/` directory. A path
  sent by any caller is ignored.
- It listens on `127.0.0.1` only, port 17777 by default, and every request
  needs the bridge token.
- It never returns session material to anything, including airdate.
- **It creates drafts. It cannot publish, schedule, or email subscribers.**
  `tests/test_draft_only.py` in the airdate repo fails if this file ever gains
  a way to do any of those.

## Requirements

- Obsidian desktop 1.11.4 or newer. Desktop only; it will not load on mobile.
- airdate, installed and paired.
- A Substack account.

## Updating

When you update airdate, re-run `python3 scripts/install-connector`, then
toggle **airdate connector** off and back on under Settings, Community
plugins. Obsidian holds the old plugin code in memory until you do, and
restarting Obsidian itself is not enough.

## Source

One file, `main.js`, 291 lines, not minified or bundled. Read it. The header
comment states the trust model it implements.

## License

MIT. See [LICENSE](LICENSE).
