"""Contract for the read-only babysit snapshot (docs/skills/scripts/pr-babysit.py).

The load-bearing question the snapshot answers is "has the review bot seen the
CURRENT head?", because a clean-looking CI on an unreviewed head is the classic
false ready. The bot leaves two different signals and only one of them exists in
the clean case, so both are pinned here.
"""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = (Path(__file__).resolve().parents[1]
          / "docs" / "skills" / "scripts" / "pr-babysit.py")

HEAD = "a" * 40
OLD = "b" * 40


@pytest.fixture(scope="module")
def babysit():
    spec = importlib.util.spec_from_file_location("pr_babysit", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def fakes(babysit, monkeypatch):
    """Install fake GitHub reads; returns a setter for the bot's signals."""
    state = {"reviews": [], "check_runs": [], "head": HEAD}

    def fake_gh(args):
        if args[:2] == ["pr", "view"] and "--json" in args:
            return state["head"] + "\n", None
        raise AssertionError(f"unexpected gh call: {args}")

    def fake_gh_json(args):
        cmd = " ".join(args)
        if cmd.startswith("pr view"):
            return {"state": "OPEN", "mergeable": "MERGEABLE",
                    "reviewDecision": None}, None
        if cmd.endswith("/reviews --paginate"):
            return state["reviews"], None
        if cmd.endswith("/check-runs"):
            return {"check_runs": state["check_runs"]}, None
        if cmd.endswith("/comments --paginate"):
            return [], None
        raise AssertionError(f"unexpected gh_json call: {cmd}")

    def fake_graphql(query, variables=None):
        return {"data": {"repository": {"pullRequest": {"reviewThreads":
                {"nodes": []}}}}}, None

    monkeypatch.setattr(babysit, "gh", fake_gh)
    monkeypatch.setattr(babysit, "gh_json", fake_gh_json)
    monkeypatch.setattr(babysit, "gh_graphql", fake_graphql)

    def set_signals(*, reviews=(), check_runs=(), head=HEAD):
        state["reviews"] = list(reviews)
        state["check_runs"] = list(check_runs)
        state["head"] = head

    return set_signals


def _review(commit):
    return {"user": {"login": "greptile-apps[bot]"}, "state": "COMMENTED",
            "commit_id": commit, "body": ""}


def _check(conclusion="success", status="completed", name="Greptile Review"):
    return {"name": name, "status": status, "conclusion": conclusion}


def test_clean_pass_is_certified_by_the_check_run(babysit, fakes):
    """Red-before: a clean run submits no review, so reviews-alone said 'unknown'.

    That is the exact case the snapshot exists to certify, so the bot's check run
    on the head commit (which is what a clean pass leaves behind) must count.
    """
    fakes(reviews=[], check_runs=[_check()])
    rep = babysit.snapshot("1", "owner/repo")
    assert rep["bot_reviewed_head"] is True
    assert rep["bot_review_evidence"] == "check-run"
    assert rep["bot_check_state"] == "success"
    # A missing review submission is the normal clean case, not an error.
    assert rep["errors"] == []


def test_review_submission_on_head_is_authoritative(babysit, fakes):
    fakes(reviews=[_review(HEAD)], check_runs=[])
    rep = babysit.snapshot("1", "owner/repo")
    assert rep["bot_reviewed_head"] is True
    assert rep["bot_review_evidence"] == "review"


def test_review_on_an_older_commit_is_stale(babysit, fakes):
    fakes(reviews=[_review(OLD)], check_runs=[])
    rep = babysit.snapshot("1", "owner/repo")
    assert rep["bot_reviewed_head"] is False
    assert rep["bot_review_evidence"] == "stale-review"


def test_running_check_on_head_is_not_a_completed_review(babysit, fakes):
    """A run still in flight must not read as ready, even with no comments yet."""
    fakes(reviews=[], check_runs=[_check(status="in_progress", conclusion=None)])
    rep = babysit.snapshot("1", "owner/repo")
    assert rep["bot_reviewed_head"] is False
    assert rep["bot_review_evidence"] == "check-pending"


def test_failed_bot_check_still_means_the_head_was_reviewed(babysit, fakes):
    """A findings run is a completed review; the unresolved comments carry the findings."""
    fakes(reviews=[], check_runs=[_check(conclusion="failure")])
    rep = babysit.snapshot("1", "owner/repo")
    assert rep["bot_reviewed_head"] is True
    assert rep["bot_review_evidence"] == "check-run"


@pytest.mark.parametrize("conclusion", [
    "cancelled", "timed_out", "startup_failure", "neutral", "skipped",
])
def test_aborted_bot_run_does_not_certify_the_head(babysit, fakes, conclusion):
    """Red-before: every non-success conclusion was folded into 'failure'.

    A run that stopped early (cancelled, timed out, crashed at startup) did not
    look at the head, so it must not read as 'snapshot complete'.
    """
    fakes(reviews=[], check_runs=[_check(conclusion=conclusion)])
    rep = babysit.snapshot("1", "owner/repo")
    assert rep["bot_reviewed_head"] is False
    assert rep["bot_review_evidence"] == "check-aborted"
    assert rep["bot_check_conclusion"] == conclusion


def test_no_bot_signal_anywhere_stays_unknown(babysit, fakes):
    fakes(reviews=[], check_runs=[_check(name="ruff")])
    rep = babysit.snapshot("1", "owner/repo")
    assert rep["bot_reviewed_head"] is None
    assert rep["bot_review_evidence"] is None
