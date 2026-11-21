# Skill: PII check before commit and PR

The rule is in `docs/PII-RULES.md`. This is the procedure that applies it.

## Before every commit

1. Sweep the staged diff, file by file:

```bash
python3 scripts/security-sweep.py --untracked
git diff --cached --name-only --diff-filter=d | while read f; do
  python3 scripts/security-sweep.py "$f" || echo "PII in: $f"
done
```

2. If anything is flagged, fix it in the file, `git add` again, and re-run. Do not proceed
   on a non-zero exit.

3. Read the commit message before committing. The message is a published artifact too: no
   hashes, no session ids, no hostnames, no real paths.

## Before every push

1. Run the sweep over the full working tree and untracked files (step 1 above with no path
   argument). An untracked doc is the most common carrier — it is written and edited but
   never added, so a `git diff` does not show it.

2. Confirm the ignore rules actually match, by path:

```bash
git check-ignore -v .env.bak.<stamp> secrets/ docs/identity-registry-local.txt
```

A match prints the rule and its line. No match exits non-zero and prints nothing. After any
`.gitignore` edit, run this for each intended path; a pattern can look correct and silently
not match.

3. Assert that the remaining untracked entries are only what you expect:

```bash
git status --short | grep '^??'
```

An unexplained entry is a blocker, not noise.

## Before opening or updating a PR

1. Sweep the PR body the same way you sweep a commit message. PR bodies are public and are
   the easiest place to paste a log excerpt.

2. Check whether a PR already exists for your head branch before creating one:

```bash
gh pr view --json number,state,baseRefName
```

`gh pr create` is not idempotent: with an open PR for the branch it errors and prints the
existing number, which reads like failure while the PR is in fact live. Push to the branch
instead (commits flow into the open PR) and report that number. Never open a duplicate.

## If the sweep finds a leak already committed

Do not rewrite history on your own initiative. Report the blast radius — commits rehashed,
refs affected, whether clones or forks exist — and let the human decide. See the last
section of `docs/PII-RULES.md`.
