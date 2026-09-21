# Setting up airdate

airdate is a local essay manager for writers who keep their essays in Obsidian
and publish on Substack. It reads a folder of Markdown notes in your vault,
tracks each essay from first draft to published, and sends a finished essay to
Substack as a **draft**.

## The promise: drafts only

airdate creates Substack drafts and stops. It cannot publish, schedule, or
email your subscribers. There is no setting that changes this. You open the
draft in Substack, look it over, and press publish yourself.

This is checked in code, not just intended. `tests/test_draft_only.py` reads
the three files that could ever reach Substack and fails if the obvious way in
appears: a call to a python-substack `Api` method outside a reviewed allowlist,
or any call whose name looks like publishing, scheduling or sending. It is a
tripwire on the front door, not a proof that no door exists, so review is still
what stands behind it. Run it yourself:

```bash
python3 -m unittest tests.test_draft_only
```

## The honest part: how airdate talks to Substack

- **The Substack client is unofficial.** airdate uses
  [python-substack](https://pypi.org/project/python-substack/) (MIT license),
  a community library that drives the same private endpoints Substack's web
  editor uses. It is not made or supported by Substack.
- **It authenticates with your Substack session cookie**, the same one your
  browser holds when you are signed in. There is no official API key to use
  instead: as of September 2026, Substack's official APIs (the Developer API
  and the Publisher API) read profile, post, and subscriber data, and neither
  documents a way to create drafts. Check Substack's current documentation if
  that matters to you; it may change.
- **It can break without warning.** If Substack changes its editor, sending
  may fail until the library catches up. `requirements-substack.txt` pins a
  known-good set of versions; that pin is what you roll back to.
- **The risk is your account, not just a failed send.** Driving private
  endpoints with a session cookie is not something Substack's terms
  contemplate, and the realistic worst case is not "sending stops working", it
  is Substack restricting or suspending the account you publish from. Nobody
  has reported that happening because of airdate, and I cannot promise it
  will not. airdate creates one draft per essay, at the pace a person writes,
  which is the same thing your browser does when you use the editor. That is
  the honest shape of it.
- **It is your call.** If the publication matters more than the convenience,
  copy and paste into Substack's editor instead. airdate is still useful for
  everything up to that point.

## Where your Substack session lives

- The session cookie is captured by the **airdate connector**, a small Obsidian
  plugin, when you sign in to Substack in a window it opens. It is stored in
  Obsidian's secret storage on this computer.
- airdate itself never sees or stores it. It is not in airdate's files, not in
  your vault's files, and not in git.
- When you press send, the connector starts `substack_draft.py` with the
  session in that one process's environment, for the length of that one send.
- airdate and the connector trust each other through a **bridge token** created
  when you pair them. It lives in Obsidian's secret storage and in
  `.airdate-data/secrets/connector.json` (readable only by you). It is never
  written into the vault, so syncing or committing your vault does not carry it.
- The connector only runs `substack_draft.py` from the airdate folder you
  paired, with the Python you paired, on files inside airdate's `drafts/`
  folder. Nothing a caller sends can change that.

## Requirements

- macOS. (Linux may work but is untested; Windows is not supported.)
- Obsidian desktop 1.11.4 or newer, with a vault.
- Python 3.10 or newer for the Substack client. macOS's built-in `python3` is
  3.9, which runs the airdate server but not the client, so you need to install
  a newer one. Two ways, either is fine:

  **The installer**, if you would rather not use a package manager: download
  the current macOS installer from [python.org/downloads](https://www.python.org/downloads/)
  and run it. Nothing else to set up.

  **Homebrew**, if you already use it or want to: `brew install python@3.12`.
  If `brew` is not a command on your machine you do not have Homebrew yet;
  install it first from [brew.sh](https://brew.sh), then run that line.

  `scripts/setup` finds any Python 3.10 through 3.13 on your PATH by itself, so
  once one is installed there is nothing to configure.
- A Substack account you can sign in to.

## Install

```bash
git clone https://github.com/seanjosephs/airdate-public.git airdate
cd airdate
scripts/setup
./run-airdate.command
```

`scripts/setup` creates `.venv-substack` from the pinned requirements and a
private `.airdate-data` folder. It changes nothing outside the airdate folder
and is safe to run again.

`run-airdate.command` starts airdate on port 8787 and opens
`http://127.0.0.1:8787/airdate`. Double-clicking it in Finder works too. Stop
it with Control-C. To use another port: `AIR_DATE_PORT=8788 ./run-airdate.command`.

## First run

The first time airdate opens, a setup wizard walks you through it one step at
a time, with back and next. Nothing is saved until you press finish, with one
exception noted in step 2.

1. **Welcome**: what airdate does, and the promise that it only ever creates
   drafts.
2. **Your vault**: the full path to the folder that contains `.obsidian`; the
   essays folder inside it (default `Essays`), or tick "my essays are at the
   vault root"; and the vault name, filled in from the folder name and used
   for "open in Obsidian" links. If the essays folder does not exist yet,
   airdate offers to create it. That empty folder is the only thing first run
   ever writes to your vault, and the only thing written before finish.
3. **Your publication**: for example `yourname.substack.com` or your custom
   domain, and a display name for thumbnail prompts. You can leave it for
   later; sending stays blocked until it is set.
4. **How you organize**: whether you keep essays in topic folders. Yes makes
   each folder inside your essays folder a category, and an essay outside any
   folder asks to be filed. No turns categories off and nothing asks to be
   filed.
5. **Your totems**: five placeholder marks (circle, triangle, square, diamond,
   star) for telling your kinds of work apart. Rename them, recolor them, and
   swap any icon for an image in your vault, such as
   `Essays/_assets/airdate/fox.png`. Or turn them off.
6. **Your tags**: optional tag presets, each a name, a color and up to five
   tags. The editor adds a preset's tags in one click. It starts empty.
7. **Your calendar**: your publish day, with Monday preselected. One weekday
   gives you a calendar with one slot per week on that day. "none" hides the
   calendar; you still pick a date when an essay is ready for air.
8. **Connect Substack**: the three Obsidian steps from the next section, the
   folder the connector is copied to, and a live check of each step. "I'll do
   this later" is fine; sending stays blocked until it is done.
9. **Finish**: a summary of your choices. Finish writes
   `.airdate-data/config.json` and opens your essays.

A short tour follows: nine stops, each pointing at one part of the screen.
Skip it whenever you like, and replay it from the top of settings.

Everything from the wizard stays editable in settings, under the same
headings. If `config.json` already exists but needs fixing (a moved vault,
say), airdate opens settings with the problem named instead of the wizard.

## Connect Substack through Obsidian

The wizard's connect step shows these same steps and checks them live. During
or after first run:

```bash
python3 scripts/install-connector
```

This copies the connector into `<vault>/.obsidian/plugins/airdate-connector/`.
The connector is not in Obsidian's community plugin directory, so Obsidian
treats it like any plugin you install by hand. It is deliberately not submitted
there: it does nothing without airdate installed and paired, and keeping the
two in one repository means they always move together. Its source is one
readable file, and
[obsidian-airdate-connector/README.md](obsidian-airdate-connector/README.md)
describes what it stores and what it will run. Then, in Obsidian:

1. Settings, Community plugins: turn off Restricted mode if it is on, then
   enable **airdate connector**.
2. Command palette: **Pair with airdate**. Enter the full path to your airdate
   folder. Leave Python and the data folder blank unless you moved them. Leave
   the port at 17777 unless something else uses it, such as the connector in
   another vault; if it does, pick another number here.
3. Command palette: **Connect Substack for airdate**, and sign in to Substack
   in the window that opens (Google sign-in works).

Reload airdate's settings page. It should say it is connected through
Obsidian. Obsidian has to be open for sending to work.

To sign out, or to switch to a different Substack account, run **Disconnect
Substack for airdate**, then **Connect Substack for airdate** again.

After updating airdate, run `python3 scripts/install-connector` again, then
reload the connector: **Settings → Community plugins**, and toggle **airdate
connector** off and back on. Obsidian keeps the old plugin code in memory
until you do, so a new command will not appear in the command palette.
Reloading the app itself does not reload the plugin.

## What airdate expects in your vault

- **Every `.md` file under the essays folder is an essay**, except notes
  whose frontmatter `type` (or a tag) is `index`, `moc`, `meta`, or
  `research`. You can hide more by filename prefix or title word in
  `config.json` (`vault.hidden`).
- **Two folders belong to airdate**, directly inside the essays folder:
  `Published/` and `Archive/`. airdate moves an essay there when you mark it
  published or archived, and moves it back out if you change your mind. A
  file in one of those folders is published or archived, whatever its
  frontmatter says.
- **Categories are the other top-level folders** inside the essays folder
  (folders starting with `_` are ignored). Filing an essay moves it into one.
  To keep your own folder layout, choose "no folders" under "how you organize"
  in settings. If your essays folder is the vault root, every top-level
  folder in the vault counts as a category, so turning categories off is
  usually the better fit.
- **Totems.** Every card has a totem slot. Click it to pick one of your five
  or none; the choice is saved to the note's `totem` key. A value airdate does
  not recognize shows as "?" and is left alone until you pick.
- **Images you attach** are written to `<essays folder>/_assets/substack/`.
- **Frontmatter.** airdate reads and writes these keys and keeps every other
  key you have exactly as it was: `title`, `subtitle`, `summary`, `status`,
  `totem`, `category`, `published_date`, `substack_url`, `substack_draft_id`,
  `substack_draft_url`, `source_role`, `draft_of`, `publication`, `slug`,
  `section`, `tags`, `post_type`, `audience`, `publish_on_web`, `send_email`,
  `free_preview`, `email_subject`, `email_preview_text`,
  `test_email_recipients`, `comment_permissions`, `scheduled_at`,
  `free_unlock_at`, `canonical_url`, `seo_title`, `seo_description`,
  `social_title`, `social_description`, `social_image`, `hero`,
  `hero_image`, `source_note`, `thumbnail_prompt`, `thumbnail_alt`, `notes`,
  `airdate_uid`. A note with no frontmatter is fine; it gets some the first
  time you file or save it.
- **`airdate_uid`** is a 32-character id airdate stamps on a note the first
  time it writes to it. It keeps the essay's identity when you move or rename
  the file. Do not edit it or copy it into another note. If two notes ever
  share one, the older file keeps it and the other gets a new one.
- **What airdate never does:** delete a note, or touch anything outside the
  essays folder (besides reading totem images you point it at).

## The lifecycle

Every essay starts in the **Writers Room**. When the writing is done it moves
to **Writers Likey**. Giving it a date (the ready for air button, or dragging it
onto the calendar) makes it **Ready for Air**. Sending the draft to Substack
makes it **Live**. When you have published it in Substack, mark it
**Published** and paste the post link; it moves to the shelf. **Archived**
sits outside the flow.

The catalog's state filter follows the same stages, plus "needs attention":
essays that need filing, are missing metadata, or are missing only a hero
image.

## Thumbnails

airdate has no AI inside it. "Copy thumbnail prompt" puts a styled prompt on
your clipboard and opens ChatGPT; you generate the image there and drag it onto
the hero field. Change the prompt's style line in `config.json`
(`thumbnail.style_prompt`; `{publication_name}` is filled in for you).

## Let your AI fill in the metadata

airdate can hold a lot of detail about an essay: a subtitle, a summary, tags,
an email subject, search and social text, a thumbnail description. You do not
have to type any of it. If you use an AI assistant that can read and write your
Obsidian vault (Claude, ChatGPT, or another one, connected through an Obsidian
MCP server or a similar plugin), it can read the essay and fill the blanks.

You stay in charge. The prompt below tells the AI to show you what it would
write before it writes anything, to fill only what is empty, and to leave alone
everything airdate manages itself.
Treat what comes back as a first draft: read it, and change whatever does
not sound like you.

### Before you start

- Know the path of the note inside your vault, for example
  `Essays/My Essay.md`.
- The essay needs a title and a body. Everything else can be blank.

### The short version (simplified metadata)

Paste this, with your note's path in the first line.

```text
Read the note at [PATH TO YOUR ESSAY] in my Obsidian vault. It is an essay I
publish with a tool called airdate, which reads the note's YAML frontmatter.

Fill in only these three frontmatter keys, and only where they are missing or
empty:

- subtitle: one line that sits under the title. It should add something the
  title does not say. No more than about 120 characters.
- summary: two or three plain sentences saying what the essay is about. It
  appears on the essay's card and as the preview line in the email.
- tags: up to five, lowercase, one or two words each, as a YAML list. Choose
  what the essay is actually about, not what would get clicks.

Rules:
1. Take everything from the essay itself. Do not invent facts, quotes or
   claims that are not in it. Match its voice; do not make it sound like
   marketing.
2. Do not change the essay body, not a word.
3. Do not change any key that already has a value, and do not touch any key I
   did not list.
4. Never write a key that is already there. Each key appears once.
5. Show me the exact lines you plan to add, and wait for my yes before you
   write to the note.
```

### The full version (complete metadata)

```text
Read the note at [PATH TO YOUR ESSAY] in my Obsidian vault. It is an essay I
publish with a tool called airdate, which reads the note's YAML frontmatter
and sends the essay to Substack as a draft.

Fill in the frontmatter keys below, and only where they are missing or empty.

About the essay
- subtitle: one line under the title that adds something the title does not
  say. About 120 characters at most.
- summary: two or three plain sentences on what the essay is about. Shown on
  the essay's card and used as the email preview if no other preview is set.
- tags: up to five, lowercase, one or two words each, as a YAML list.
- slug: the web address ending. Lowercase words joined by hyphens, taken from
  the title, short enough to read aloud. Skip this if the title already makes
  a good one; airdate derives it from the title when it is blank.

Email
- email_subject: the subject line subscribers see. It can be the title. If you
  write a different one, keep it under about 60 characters and make it honest
  about what is inside.
- email_preview_text: the grey line after the subject in an inbox. One
  sentence, about 90 characters, that does not repeat the subject.

Search and sharing
- seo_title: the title as a search result. About 60 characters at most.
- seo_description: one or two sentences for the search result. About 155
  characters at most.
- social_title: the title as it appears when the link is shared. Often the
  same as the title.
- social_description: one sentence for the share card.

Image
- thumbnail_prompt: a description of an image that would suit this essay,
  written so that I could hand it to an image generator or an illustrator.
  Describe the subject, the mood and the composition. Do not ask for text in
  the image.
- thumbnail_alt: one sentence describing that image for someone who cannot
  see it.

Leave these keys alone, whatever they contain. airdate manages them:
airdate_uid, status, scheduled_at, substack_draft_id, substack_draft_url,
substack_url, published_date, source_role, draft_of, hero, hero_image,
publication, audience, send_email, publish_on_web, comment_permissions.

Rules:
1. Take everything from the essay itself. Do not invent facts, quotes or
   claims that are not in it. Match its voice; do not make it sound like
   marketing.
2. Do not change the essay body, not a word.
3. Do not change any key that already has a value, and do not touch any key
   that is not in the list above.
4. Never write a key that is already there. Each key appears once in the
   frontmatter.
5. Keep the frontmatter valid YAML. Put text values in double quotes.
6. Show me the exact lines you plan to add, and wait for my yes before you
   write to the note.
```

### After it runs

Open the essay in airdate. Everything the AI wrote is there to read and
change. Press check readiness: if a key ended up in the note twice, airdate
says so there.

### Worth knowing

- The AI reads your whole essay to do this. If the essay is private until it
  is published, use an assistant you trust with it.
- airdate never sends anything to an AI. This all happens between your
  assistant and your vault; airdate only reads the note afterward.

## Optional settings in config.json

`config.example.json` shows every key. Beyond the settings page you can set:

- `totems.items[].keywords`: words that suggest a totem for an essay that has
  none, for example `{"debate": 3}`. `totems.default`: the totem for essays
  that match nothing.
- `categories.items`: category folders with keywords for filing suggestions.
- `links`: extra links in the sidebar.
- `connector.port`: the port airdate expects before pairing. After
  pairing, airdate uses the port you chose in **Pair with airdate**.

Restart airdate after editing the file by hand.

## Running on another machine or port

airdate binds to `127.0.0.1` and needs no password there. To reach it from
elsewhere, set `HOST`, `AIR_DATE_AUTH_USER`, and `AIR_DATE_AUTH_PASSWORD`
(for example in `.env.local`, which the launcher reads). To require the
password on this computer too, set `AIR_DATE_AUTH_REQUIRED=true`. Whenever a
password is required, local folder paths are hidden from the browser. airdate refuses to
bind beyond loopback without a password, and in that mode the vault folder can
only be set in `config.json`, not from the browser. Put HTTPS in front of it
before using it over a network.

## Troubleshooting

- **"Obsidian connector is not paired"**: run `scripts/install-connector`,
  enable the plugin, then run **Pair with airdate** in Obsidian.
- **"Open Obsidian with the airdate connector enabled"**: Obsidian is closed,
  or the plugin is off.
- **Send fails with an auth error**: the Substack session expired. Run
  **Connect Substack for airdate** again.
- **Wrong Substack account**: run **Disconnect Substack for airdate**, then
  **Connect Substack for airdate** and sign in with the right one.
- **"python-substack not importable"**: run `scripts/setup`.
- **Port in use**: another program has 8787. Start airdate with
  `AIR_DATE_PORT=8788 ./run-airdate.command`.
- **A send timed out**: check Substack for the draft before sending again; it
  may have been created.
- **Double-clicking `run-airdate.command` does not start airdate**, whether it
  fails silently or macOS puts up a warning: you downloaded the ZIP through a
  browser, so macOS quarantined it, and the launcher is not code-signed. Either
  clone the repository instead, which carries no quarantine, or clear it on the
  folder you already have:

  ```bash
  xattr -dr com.apple.quarantine /path/to/airdate
  ```

  Only do that for a folder you fetched yourself from the airdate repository.

## Tests

```bash
python3 -m unittest discover -s tests
python3 scripts/workflow-smoke.py
python3 scripts/frontmatter-roundtrip-test.py
python3 scripts/obsidian-fidelity-test.py
```

None of them touch the network or your vault. The smoke test starts airdate
against a throwaway vault and a fake connector.
