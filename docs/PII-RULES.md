# PII rules

This repo is treated as **public** for all artifact rules, regardless of its current GitHub
visibility. It is private at present and may be made public again; the rules do not relax
while it is private. Follow them in every artifact you produce: docs, code, tests, comments,
commit messages, branch names, and PR bodies.

## What must never appear

- **RNS/LXMF identity hashes**, in any form: full 32-hex values and any 8+ hex prefix.
  A truncated hash with an ellipsis (`<hash>..`) is still a fingerprint, not redacted.
- **Session ids and chat ids** used as examples. These look like
  `<date>_<time>_<suffix>` or a 9-11 digit number.
- **Hostnames and endpoints**: Tailscale MagicDNS names, `host:port` values, absolute home
  paths.
- **Key material**: anything matching a `-----BEGIN ... -----` PEM header.

Real values live only in gitignored files (`.env`, `logs/`, `docs/identity-registry-local.txt`).
In tracked files, use role labels and placeholders: `<peer-A>`, `<session-id>`,
`<timestamp>`, `0.0.0.0:<port>`.

## The enforcement gate

Run the sweep before every commit and every push:

```bash
python3 scripts/security-sweep.py --untracked
```

Exit code 0 = clean. Exit 1 = findings, and you do not commit or push until they are
resolved. Do not add a value to the script's `SYNTHETIC` allowlist to make a real finding
pass; the allowlist is for obviously fake fixtures only.

Two gates enforce this automatically:

- **Local pre-push hook.** `scripts/install-git-hooks.sh` sets `core.hooksPath` to the
  committed `.githooks/` directory; `.githooks/pre-push` runs the sweep with `--untracked`
  and blocks the push on any finding. Run the installer once per clone.
- **CI.** The `pii-sweep` job in `.github/workflows/ci.yml` runs the same sweep on every
  push and pull request into `dev`/`main`, so a push that skips the local hook still fails
  the PR check.

Both gates scan the **commits being pushed**, not only the final tree. A leak introduced in
one commit and redacted in a later commit of the same push leaves the final tree clean but
still publishes the value in the first commit, so the gates run the sweep's `--commits` mode
over the commit range the push or PR adds (`git rev-list <range>`) and check each changed
blob at that commit plus each commit message. That mode is in `scripts/security-sweep.py`;
run it by hand the same way:

```bash
python3 scripts/security-sweep.py --commits HEAD --not --remotes   # unpushed commits
python3 scripts/security-sweep.py --commits origin/dev..HEAD       # a branch's commits
```

Sweep the staged diff as well, so a leak is caught at commit time and not only at push time:

```bash
git diff --cached --name-only | while read f; do
  python3 scripts/security-sweep.py "$f" || echo "PII in: $f"
done
```

## Why this keeps happening

The leak vector is **writing a doc from a live log excerpt**. Real hashes, session ids, and
chat ids appear in logs and terminal output, and copying that text into a findings doc or a
spec example carries the identifier with it. The fix is a habit, not a cleanup: when you
quote a log line into a tracked file, redact identity values **at the moment of writing**.
Label them (`<peer-A>`, `<session-id>`) rather than pasting and intending to redact later.

Two specific traps:

- **Test fixtures.** A test that pins a 32-hex destination hash invites pasting the
  operator's real node hash in, and the test file is tracked. Use a visibly synthetic
  placeholder with the same shape (`0badc0de...`, `0123456789abcdef`) and cover the
  near-miss cases (one char short, right length but non-hex) as separate parameters. The
  allowlist in `scripts/security-sweep.py` already accepts these.
- **Finding docs.** A findings doc written from log greps leaks exactly as much as a test
  fixture. Sweep the diff before committing, not just before pushing.

## If you find a leak in existing history

Do not rewrite history on your own initiative. A rewrite rehashes every descendant commit
and needs a force-push of every branch, invalidating every clone. State the blast radius
(commits rehashed, refs affected, whether clones or forks exist) and let the human decide.

Note: force-pushing branches does not remove commits retained under GitHub's closed-PR
`refs/pull/*/head` refs. That residual is a platform behavior, not something a branch push
fixes.
