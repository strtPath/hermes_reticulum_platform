"""Card A19 — license posture: exact pins, terms where users meet them.

ADR-0002 rules that rns and lxmf are pinned to exact PyPI versions rather than
vendored, and that their conditions are shown where a user actually meets them.
pip prints nothing license-shaped, so the disclosure lives in README.md and
install.sh. These tests assert all three halves mechanically:

1. pyproject's rns/lxmf pins are exact suffixes and equal the uv.lock pair, so a
   range can never reopen the "resolved version nobody read" gap the ADR closed.
2. The condition sentences and the canonical license URL appear verbatim in both
   the README License section and the installer, ahead of install.sh's pip step.
3. The vendoring nets stay empty: no RNS/LXMF source blob by content or by name.

Quoted text is compared as literals taken from
https://reticulum.network/manual/license.html, not from memory.
"""

import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
UV_LOCK = REPO_ROOT / "uv.lock"
README = REPO_ROOT / "README.md"
INSTALLER = REPO_ROOT / "install.sh"

LICENSE_URL = "https://reticulum.network/manual/license.html"

# The Reticulum License's two extra field-of-use conditions plus the notice
# clause, verbatim. Tests assert these as single lines in both files: a wrapped
# quote would still read as present to a reviewer but would drift silently.
CONDITIONS = (
    "The Software shall not be used in any kind of system which includes amongst "
    "its functions the ability to purposefully do harm to human beings.",
    "The Software shall not be used, directly or indirectly, in the creation of an "
    "artificial intelligence, machine learning or language model training dataset, "
    "including but not limited to any use that contributes to the training or "
    "development of such a model or algorithm.",
    "The above copyright notice and this permission notice shall be included in "
    "all copies or substantial portions of the Software.",
)


def _dependencies() -> dict[str, str]:
    with PYPROJECT.open("rb") as handle:
        data = tomllib.load(handle)
    return {
        entry.split("=", 1)[0].strip(): entry
        for entry in data["project"]["dependencies"]
    }


def _locked_version(name: str) -> str:
    with UV_LOCK.open("rb") as handle:
        data = tomllib.load(handle)
    for package in data["package"]:
        if package["name"] == name:
            return package["version"]
    raise AssertionError(f"{name} is not in uv.lock")


@pytest.mark.parametrize("name", ["rns", "lxmf"])
def test_pin_is_exact_not_a_range(name):
    spec = _dependencies()[name]
    assert re.fullmatch(rf"{name}==\d+\.\d+\.\d+", spec), (
        f"{name} must be pinned exactly per ADR-0002, got {spec!r}; "
        "a range can resolve to a version whose source nobody here has read"
    )


@pytest.mark.parametrize("name", ["rns", "lxmf"])
def test_pin_matches_uv_lock(name):
    spec = _dependencies()[name]
    assert spec == f"{name}=={_locked_version(name)}"


def test_readme_states_the_conditions_verbatim():
    text = README.read_text(encoding="utf-8")
    license_section = text.split("## License", 1)[1].split("\n## ", 1)[0]
    for condition in CONDITIONS:
        assert condition in license_section
    assert LICENSE_URL in license_section


def test_installer_states_the_conditions_verbatim():
    text = INSTALLER.read_text(encoding="utf-8")
    for condition in CONDITIONS:
        assert condition in text
    assert LICENSE_URL in text


@pytest.mark.parametrize("marker", ["pip install -e", "pip install --break-system-packages -e"])
def test_installer_notice_precedes_the_pip_step(marker):
    text = INSTALLER.read_text(encoding="utf-8")
    notice = text.index(LICENSE_URL)
    assert notice < text.index(marker), (
        "the notice must be printed before the install step, or it is never seen"
    )


def _git(*args: str, check: bool = True) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=check
    ).stdout


needs_git = pytest.mark.skipif(
    shutil.which("git") is None or not (REPO_ROOT / ".git").exists(),
    reason="vendoring nets read the git index",
)


@needs_git
def test_no_vendored_source_by_content():
    """Content net: an RNS/LXMF source blob would carry the upstream header.

    ``git grep`` exits 1 when nothing matches — that is the passing case, so the
    call runs unchecked and the empty output is the assertion.
    """
    matching = [
        path
        for path in _git(
            "grep", "-l", "-E", "This file is part of RNS|^# Reticulum License",
            check=False,
        ).splitlines()
        if not path.startswith("docs/")
    ]
    assert matching == []


@needs_git
def test_no_vendored_source_by_name():
    """Names net: vendored dirs, renamed modules, and wheel/sdist archives."""
    pattern = re.compile(r"(^|/)(rns|lxmf)[-/_.]|\.(whl|tar\.gz)$", re.IGNORECASE)
    offenders = [path for path in _git("ls-files").splitlines() if pattern.search(path)]
    assert offenders == []
