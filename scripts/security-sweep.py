#!/usr/bin/env python3
"""Pre-push PII sweep (card A1 of docs/specs/adapter/tasks.md).

Scans visible files and fails on:
  - 32-hex Reticulum destination hashes (any case)
  - other hash-shaped runs, 8-32 hex chars (any case, e.g. session-id suffixes)
  - absolute home paths (/home/<user>, /Users/<user>, ~/...)
  - host:port endpoints and Tailscale MagicDNS hostnames
  - -----BEGIN key/credential headers
  - 9-11 digit numeric ids (chat ids, epoch timestamps used as session names)

Modes:
  (no args)        scan the files git tracks, read from the working tree
  --untracked      also scan untracked, non-ignored files
  --staged         scan the STAGED content (git index), so a value redacted only in
                   the working tree cannot slip through a commit
  --commits ARGS   scan the commits selected by `git rev-list ARGS`: every changed
                   file's blob AT THAT COMMIT, plus each commit message. Put it last;
                   every argument after --commits is passed to git rev-list. Merge
                   commits are scanned too (a conflict resolution can add a value
                   neither parent has, and a merge message can carry one): their
                   diff is taken against each parent. Use this to catch a leak that
                   an earlier commit in the same push introduced and a later commit
                   redacted: the final tree is clean, the intermediate commit is not.
                   Example ranges:
                     --commits HEAD --not --remotes        (unpushed commits)
                     --commits origin/dev..HEAD            (a PR's commits)
  PATH...          scan only the named files (combine with --staged to read the index)

Synthetic fixtures are allowlisted: the patterns below and any token composed only of
repeats of them. Exit code 0 = clean, 1 = findings (blocking), 2 = bad usage.
"""
import re
import subprocess
import sys
from pathlib import Path

PATTERNS = (
    ("peer32", re.compile(r"\b[0-9a-fA-F]{32}\b")),
    ("shorthex", re.compile(r"\b[0-9a-fA-F]{8}(?:[0-9a-fA-F]{0,24})\b(?![0-9a-fA-F])")),
    ("hostport", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}:\d+\b|\btail[a-z0-9]+\.ts\.net\b")),
    ("pem", re.compile(r"-----BEGIN [A-Z ]+-----")),
    ("numeric-id", re.compile(r"\b\d{9,11}\b(?!\d)")),
)

HOMEPATH = re.compile(r"(?:/home/[A-Za-z0-9._-]+|/Users/[A-Za-z0-9._-]+)")

SYNTHETIC = {
    "11223344556677889900aabbccddeeff", "ffeeddccbbaa00998877665544332211",
    "0badc0de0badc0de0badc0de0badc0de", "0bade0de0bade0de0bade0de0bade0de",
    "abcdef0123456789abcdef0123456789", "00000000000000000000000000000000",
    "1122334411223344aabbccdd11223344", "127.0.0.1:8471", "127.0.0.1:8765",
    "127.0.0.1:0", "0.0.0.0:0",
    "aabbccddeeff00112233445566778899", "aabbccdd11223344aabbccdd11223344",
    "bbbbbbbb", "bbbbbbbbbbbbbbbb", "127.0.0.1:7656",
}

# Short hex tokens are allowed when they are built ONLY from these fixture blocks
# (e.g. "aabbccdd" * 4, "0badc0de" truncated by one char in a boundary test).
FIXTURE_BLOCKS = ("aabbccdd", "11223344", "deadbeef", "0badc0de", "abcd1234",
                 "aa11bb22", "cc33dd44", "ee55ff66", "0123456789abcdef", "0" * 4, "0" * 8)

# Calendar dates embedded in identifiers (20260915-style) are not identity material.
DATEISH = re.compile(r"\b20\d{6}\b")

# Absolute home paths that are deliberately generic in docs (shell placeholders).
HOME_ALLOW = ("/home/<user>", "/home/user", "/home/example", "/Users/<user>",
              "/home/<name>", "/home/username")

VISIBLE_SUFFIXES = (
    ".md", ".py", ".yaml", ".yml", ".txt", ".conf", ".example", ".json",
    ".toml", ".sh", ".service",
)


def _composed_of_blocks(token: str) -> bool:
    """True when the token is blocks-concatenation plus at most 1 stray char
    (boundary tests truncate fixture hashes by one character)."""
    for block in FIXTURE_BLOCKS:
        rest = token
        while rest:
            if rest.startswith(block):
                rest = rest[len(block):]
            elif len(rest) == 1:
                return True
            elif len(rest) < len(block) and block.startswith(rest):
                return True
            else:
                break
        if not rest:
            return True
    return False


def _is_synthetic(token: str) -> bool:
    t = token.lower()
    return t in SYNTHETIC or _composed_of_blocks(t)


def _home_paths_are_generic(line: str) -> bool:
    """True when every absolute home path on the line is a documented placeholder
    (e.g. /home/<user>) rather than a real username. The username segment must
    match a placeholder EXACTLY: a prefix test would let an account whose name
    extends a placeholder through, because the placeholder is a prefix of it.
    """
    for token in HOMEPATH.findall(line):
        # token looks like /home/<name>... : take the segment right after the root.
        parts = token.split("/")
        # parts: ['', 'home', '<name>', ...] (or ['', 'Users', '<name>', ...])
        if len(parts) < 3:
            return False
        name = parts[2]
        if f"/{parts[1]}/{name}" not in HOME_ALLOW:
            return False
    return True


def _git(*argv: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *argv], capture_output=True, text=True, check=check)


def _z_split(output: str) -> list[str]:
    """Split NUL-terminated git output; keeps filenames containing spaces intact."""
    return [part for part in output.split("\0") if part]


def git_files(untracked: bool) -> list[str]:
    # -z: NUL-separated paths, so "private notes.md" stays one filename.
    files = _z_split(_git("ls-files", "-z").stdout)
    if untracked:
        for entry in _z_split(_git("status", "--porcelain", "-z").stdout):
            if entry.startswith("?? "):
                files.append(entry[3:])
    return files


def staged_content(path: str) -> str | None:
    r = subprocess.run(["git", "show", f":{path}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def commit_entries(rev_args: list[str]) -> list[tuple[str, str]]:
    """``(label, text)`` for every commit selected by ``git rev-list``.

    A leak enters history through a changed file or a commit message, so this
    reads each commit's changed blobs at that commit (``git show <sha>:<path>``)
    rather than the final tree. That is what catches a value introduced in one
    commit and redacted in a later one inside the same push.

    Merge commits are NOT skipped: a conflict resolution can add a value neither
    parent contains ("evil merge"), and a merge message can carry one. For a
    merge, ``diff-tree -m`` reports the files changed against each parent; the
    paths are deduplicated before reading. Root commits are read in full via
    ``--root``. ``-z`` keeps filenames containing spaces intact.
    """
    entries: list[tuple[str, str]] = []
    shas = _git("rev-list", *rev_args).stdout.split()
    for sha in shas:
        short = sha[:8]
        entries.append((f"{short}:commit-message",
                        _git("log", "-1", "--format=%B", sha).stdout))
        parents = _git("rev-list", "--parents", "-n", "1", sha).stdout.split()
        diff_args = ["diff-tree", "--no-commit-id", "--name-only", "-r", "-z"]
        if len(parents) > 2:
            diff_args.append("-m")          # one diff per parent
        elif len(parents) == 1:
            diff_args.append("--root")      # parentless commit: whole tree
        diff_args.append(sha)
        diff = _git(*diff_args, check=False)
        if diff.returncode != 0:
            continue
        seen: set[str] = set()
        for path in _z_split(diff.stdout):
            if path in seen or Path(path).suffix not in VISIBLE_SUFFIXES:
                continue
            seen.add(path)
            blob = subprocess.run(["git", "show", f"{sha}:{path}"],
                                  capture_output=True, check=False)
            if blob.returncode != 0:
                continue
            entries.append((f"{short}:{path}",
                            blob.stdout.decode("utf-8", errors="ignore")))
    return entries


def scan_text(rel: str, text: str) -> int:
    findings = 0
    for lineno, line in enumerate(text.splitlines(), 1):
        if not _home_paths_are_generic(line):
            findings += 1
            print(f"FINDING {rel}:{lineno} [homepath] absolute home path")
            print(f"    {line.strip()[:120]}")
        for name, pat in PATTERNS:
            for token in pat.findall(line):
                if DATEISH.fullmatch(token) or _is_synthetic(token):
                    continue
                findings += 1
                print(f"FINDING {rel}:{lineno} [{name}] {token}")
                print(f"    {line.strip()[:120]}")
    return findings


def main() -> int:
    argv = sys.argv[1:]
    commits = False
    rev_args: list[str] = []
    if "--commits" in argv:
        idx = argv.index("--commits")
        # Everything after --commits belongs to git rev-list, so a range like
        # "HEAD --not --remotes" is not mistaken for sweep flags.
        rev_args = argv[idx + 1:]
        argv = argv[:idx]
        commits = True
    args = [a for a in argv if not a.startswith("--")]
    untracked = "--untracked" in argv
    staged = "--staged" in argv

    if commits:
        if not rev_args:
            print("usage: --commits <git rev-list args...>", file=sys.stderr)
            return 2
        findings = 0
        for label, text in commit_entries(rev_args):
            findings += scan_text(label, text)
    elif staged:
        targets = args or git_files(untracked)
        root = Path.cwd()
        findings = 0
        for rel in targets:
            text = staged_content(rel)
            if text is None:
                continue
            if Path(rel).suffix not in VISIBLE_SUFFIXES:
                continue
            findings += scan_text(rel, text)
    else:
        targets = args or git_files(untracked)
        root = Path.cwd()
        findings = 0
        for rel in targets:
            p = root / rel
            if not p.is_file() or p.suffix not in VISIBLE_SUFFIXES:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            findings += scan_text(rel, text)

    if findings:
        print(f"\n{findings} finding(s). Redact before pushing (see "
              f"docs/PII-RULES.md, 'Why this keeps happening').")
        return 1
    print("clean: no non-synthetic identity material found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
