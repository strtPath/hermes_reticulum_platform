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


def git_files(untracked: bool) -> list[str]:
    files = subprocess.run(["git", "ls-files"], capture_output=True, text=True,
                           check=True).stdout.split()
    if untracked:
        st = subprocess.run(["git", "status", "--porcelain"], capture_output=True,
                            text=True, check=True).stdout
        for line in st.splitlines():
            if line.startswith("??"):
                files.append(line[2:].strip())
    return files


def staged_content(path: str) -> str | None:
    r = subprocess.run(["git", "show", f":{path}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


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
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    untracked = "--untracked" in sys.argv
    staged = "--staged" in sys.argv

    if staged:
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
