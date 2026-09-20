# Security

airdate runs entirely on your own machine. There is no airdate account, no
airdate server, and nothing is sent anywhere except the one Substack draft you
ask for. That keeps the attack surface small, but it is not zero, and this
page says exactly what is worth protecting and how to tell me if it breaks.

## Reporting a vulnerability

**Use [private vulnerability reporting](https://github.com/seanjosephs/airdate-public/security/advisories/new).**
It gives you a private channel with me and does not publish anything until
there is a fix.

Please do not open a public issue for anything that could expose a Substack
session, the bridge token, or a vault's contents. Ordinary bugs belong in
[public issues](https://github.com/seanjosephs/airdate-public/issues); those
are welcome and easier for everyone.

What to expect: airdate is maintained by one person as a side project. I will
acknowledge a report as soon as I see it and tell you honestly whether I can
fix it and roughly when. There is no bounty and no guaranteed response time.
If a report sits without an answer, a nudge is fair.

## What airdate holds that is worth protecting

**Your Substack session cookie.** This is the sensitive thing. It is the same
credential your browser holds when you are signed in to Substack, and anyone
who has it can act as you on Substack. It is captured by the airdate connector
when you sign in through the window it opens, and it is stored in **Obsidian's
secret storage** on that computer. airdate itself never sees it or stores it:
it is not in airdate's files, not in your vault, and not in git. When you press
send, the connector passes it to `substack_draft.py` in that one process's
environment, for the length of that one send.

**The bridge token.** airdate and the connector authenticate to each other with
a token created when you pair them. It lives in Obsidian's secret storage and
in `.airdate-data/secrets/connector.json`, which is created readable only by
you. It is deliberately never written into the vault, so syncing or committing
your vault does not carry it.

**Your vault.** airdate reads your essays folder and writes frontmatter back
into notes there. It never deletes a note, and it never touches anything
outside the essays folder besides reading totem images you point it at.

## Trust boundaries

| Boundary | How it is protected |
|---|---|
| airdate's web server | Binds `127.0.0.1` only. Refuses to bind beyond loopback without a password set. |
| The connector's HTTP endpoint | `127.0.0.1` on port 17777. Every request requires the bridge token. |
| What the connector will run | Pairing pins the airdate folder and the Python interpreter. `/draft` ignores any path a caller sends except the one draft file, which must sit inside the pinned folder's `drafts/` directory. |
| What the connector returns | Status, connect and draft results only. It never returns session material. |

## Known and accepted risks

These are design tradeoffs, documented so you can decide for yourself. They
are not bugs, and reporting them tells me nothing I do not already know.

- **The Substack client is unofficial and cookie-based.** See
  [SETUP.md](SETUP.md#the-honest-part-how-airdate-talks-to-substack) for the
  full explanation, including the account risk.
- **Any process running as your user can read
  `.airdate-data/secrets/connector.json`.** File permissions are the only
  barrier. airdate does not defend against malware already running as you.
- **On loopback, airdate requires no password by default.** Anything that can
  reach `127.0.0.1:8787` on your machine can drive airdate, including local
  folder paths. Set `AIR_DATE_AUTH_REQUIRED=true` if you want a password
  locally too.
- **Downloaded copies hit Gatekeeper.** A repository you `git clone` carries no
  quarantine attribute and runs normally. A ZIP downloaded through a browser
  does carry one, and `run-airdate.command` is not code-signed, so macOS will
  refuse to open it. Clone instead, or see
  [SETUP.md](SETUP.md#troubleshooting).

## Out of scope

- **Vulnerabilities in python-substack itself.** Report those to
  [ma2za/python-substack](https://github.com/ma2za/python-substack). If one
  affects airdate's pinned version, tell me too and I will move the pin.
- **Substack's own endpoints and their behavior.** Not mine to fix.
- **"Using an unofficial client may violate Substack's terms."** True, and
  documented in SETUP.md. That is a disclosure question, not a vulnerability.

## Supported versions

Only the latest release. `requirements-substack.txt` pins a known-good set of
dependency versions; if a newer one breaks sending, that pin is the rollback.
