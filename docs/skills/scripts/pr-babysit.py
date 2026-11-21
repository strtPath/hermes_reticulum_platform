#!/usr/bin/env python3
"""Read-only PR snapshot for the babysit loop (docs/skills/babysit-pr-for-review-bot.md).

Reports, for a PR:
  - unresolved review-bot comments (inline review comments, grouped by severity badge)
  - CI check status
  - mergeability
  - whether the bot has reviewed the CURRENT head: from its review submission
    when it has findings, or from its check run scoped to the head commit when
    it found nothing (it submits no review object in that case)

It NEVER writes to GitHub. The agent's only write is `git push` of a fix commit.

Usage:
  python3 scripts/pr-babysit.py --pr 13
  python3 scripts/pr-babysit.py --pr auto        # infer from current branch
  python3 scripts/pr-babysit.py --pr 13 --json
"""
import argparse
import json
import re
import subprocess
import sys

BOT_LOGINS = ("greptile-apps", "greptile-apps[bot]")
SEV_RE = re.compile(r'badges/(p\d)\.svg', re.I)
BOT_CHECK_RE = re.compile(r"greptile", re.I)


def gh(args):
    r = subprocess.run(["gh"] + args, capture_output=True, text=True)
    if r.returncode != 0:
        return None, r.stderr.strip()
    return r.stdout, None


def gh_json(args):
    out, err = gh(args)
    if err:
        return None, err
    if out is None:
        return None, "no output"
    try:
        return json.loads(out), None
    except json.JSONDecodeError as e:
        return None, f"bad json: {e}"


def resolve_pr(spec, repo):
    if spec != "auto":
        return spec, None
    out, err = gh(["pr", "view", "--repo", repo, "--json", "number", "--jq", ".number"])
    if err or out is None:
        return None, err or "no output"
    return out.strip(), None


def repo_of_origin():
    out, err = gh(["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"])
    return out.strip() if (not err and out) else None


def gh_graphql(query, variables=None):
    args = ["api", "graphql", "-f", f"query={query}"]
    for k, v in (variables or {}).items():
        args += ["-F", f"{k}={v}"]
    r = subprocess.run(["gh"] + args, capture_output=True, text=True)
    if r.returncode != 0:
        return None, r.stderr.strip()
    try:
        return json.loads(r.stdout), None
    except json.JSONDecodeError as e:
        return None, f"bad json: {e}"


RESOLVED_Q = """
query($owner:String!,$name:String!,$pr:Int!){
  repository(owner:$owner,name:$name){
    pullRequest(number:$pr){
      reviewThreads(first:100){
        nodes{
          isResolved
          comments(first:1){ nodes{ databaseId } }
        }
      }
    }
  }
}
"""


def resolved_comment_ids(repo, pr):
    """Map of review-comment databaseId -> isResolved, via GraphQL (REST has no flag)."""
    owner, _, name = repo.partition("/")
    data, err = gh_graphql(RESOLVED_Q, {"owner": owner, "name": name, "pr": pr})
    if err or not data:
        return {}, err or "no data"
    out = {}
    try:
        threads = data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
        for th in threads:
            c = (th.get("comments", {}).get("nodes") or [{}])[0]
            cid = c.get("databaseId")
            if cid is not None:
                out[cid] = bool(th.get("isResolved"))
    except (KeyError, TypeError) as e:
        return {}, f"parse: {e}"
    return out, None


def plain(body):
    """Greptile puts an <img> severity badge inline; drop tags for a readable excerpt."""
    text = re.sub(r'<[^>]+>', ' ', body or '')
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def sev_of(body):
    m = SEV_RE.search(body or "")
    return m.group(1).upper() if m else "?"


def is_resolved(c):
    """An inline review comment has no resolved flag in REST; resolution lives on the
    review thread (GraphQL). Treat comments as unresolved until the bot re-reviews or the
    thread is resolved; the human confirms resolution in the UI."""
    return False


def latest_bot_review(repo, pr):
    """The commit_id of the bot's most recent review, or None.

    Greptile submits one review per push, tagged with the commit it reviewed.
    The snapshot is only COMPLETE once the bot has reviewed the current head:
    otherwise a green CI reads as 'ready' while the bot is still thinking (it
    lags CI by minutes), and its findings land after the agent already called it
    clean.

    Prefer the newest review submission's commit_id. A review submission is the
    authoritative 'I reviewed SHA X' signal. Inline comments are NOT used as the
    primary signal: their ``commit_id`` tracks the PR's current head at read time
    and only ``original_commit_id`` records what was reviewed, so they are a
    weaker and easily-stale source.
    """
    reviews, err = gh_json(["api", f"repos/{repo}/pulls/{pr}/reviews", "--paginate"])
    if err:
        return None, err
    # No reviews is the normal clean case, not an error: the bot only submits a
    # review when it has findings (see ``reviewed_head_verdict``).
    bot = [r for r in (reviews or [])
           if any(b in ((r.get("user") or {}).get("login", "")) for b in BOT_LOGINS)]
    if not bot:
        return None, None
    # Reviews come oldest-first; the last one is the most recent.
    return bot[-1].get("commit_id"), None


# Conclusions that mean the bot finished looking at the commit. Everything else
# (cancelled, timed_out, startup_failure, neutral, skipped, stale) means it
# stopped early: the head was NOT reviewed, so it must not certify readiness.
REVIEW_CONCLUSIONS = {"success": "success", "failure": "failure"}


def bot_check_state(repo, head_sha):
    """``(state, name, conclusion, err)`` for the bot's check run on ``head_sha``.

    ``state`` is ``"success"``, ``"failure"``, ``"pending"``, ``"aborted"`` or
    ``None`` when the bot has no run for that commit. The run is scoped to the
    commit it read, so only a run that completed with an accepted conclusion is
    proof the bot reviewed that head.
    """
    if not head_sha:
        return None, None, None, None
    runs, err = gh_json(["api", f"repos/{repo}/commits/{head_sha}/check-runs"])
    if err or runs is None:
        return None, None, None, err or "no output"
    bot = [r for r in (runs.get("check_runs") or [])
           if BOT_CHECK_RE.search(r.get("name") or "")]
    if not bot:
        return None, None, None, None
    run = bot[-1]
    name = run.get("name")
    conclusion = run.get("conclusion")
    if run.get("status") in ("queued", "in_progress"):
        return "pending", name, conclusion, None
    return REVIEW_CONCLUSIONS.get(conclusion, "aborted"), name, conclusion, None


def reviewed_head_verdict(head_sha, reviewed_sha, check_state):
    """``(reviewed_current_head, evidence)`` from the two signals the bot leaves.

    A review submission is authoritative when it exists. But the bot only
    SUBMITS a review when it has findings: a clean pass leaves no review object
    at all, only a check run scoped to the commit it read. Reading reviews alone
    therefore reported ``unknown``/``STALE`` on exactly the clean case this
    snapshot exists to certify.

    ``evidence`` is ``"review"``, ``"check-run"``, ``"check-pending"``,
    ``"check-aborted"``, ``"stale-review"`` or ``None``.
    """
    if not head_sha:
        return None, None
    if reviewed_sha and reviewed_sha == head_sha:
        return True, "review"
    if check_state in ("success", "failure"):
        return True, "check-run"
    if check_state == "pending":
        return False, "check-pending"
    if check_state == "aborted":
        return False, "check-aborted"
    if reviewed_sha:
        return False, "stale-review"
    return None, None


def snapshot(pr, repo):
    rep = {"pr": pr, "repo": repo, "bot_comments": [], "resolved_bot_comments": [],
           "ci": None, "mergeable": None,
           "state": None, "review_decision": None, "errors": [],
           "head_sha": None, "bot_reviewed_sha": None, "bot_reviewed_head": None}

    resolved, rerr = resolved_comment_ids(repo, pr)
    if rerr:
        rep["errors"].append(f"threads: {rerr}")

    comments, err = gh_json(["api", f"repos/{repo}/pulls/{pr}/comments", "--paginate"])
    if err:
        rep["errors"].append(f"comments: {err}")
        comments = []
    for c in comments or []:
        login = (c.get("user") or {}).get("login", "")
        if not any(b in login for b in BOT_LOGINS):
            continue
        entry = {
            "id": c.get("id"),
            "severity": sev_of(c.get("body")),
            "path": c.get("path"),
            "line": c.get("line") or c.get("original_line"),
            "url": c.get("html_url"),
            "excerpt": plain(c.get("body"))[:140],
        }
        if resolved.get(c.get("id"), False):
            rep["resolved_bot_comments"].append(entry)
        else:
            rep["bot_comments"].append(entry)

    meta, err = gh_json(["pr", "view", pr, "--repo", repo,
                         "--json", "state,mergeable,reviewDecision"])
    if err or meta is None:
        rep["errors"].append(f"pr view: {err or 'no output'}")
    else:
        rep["state"] = meta.get("state")
        rep["mergeable"] = meta.get("mergeable")
        rep["review_decision"] = meta.get("reviewDecision")

    sha_out, err = gh(["pr", "view", pr, "--repo", repo, "--json", "headRefOid",
                      "--jq", ".headRefOid"])
    if not err and sha_out:
        head = sha_out.strip()
        rep["head_sha"] = head
        checks, cerr = gh_json(["api", f"repos/{repo}/commits/{head}/check-runs"])
        if cerr or checks is None:
            rep["errors"].append(f"checks: {cerr or 'no output'}")
        else:
            runs = checks.get("check_runs", [])
            rep["ci"] = {
                "total": len(runs),
                "failed": [r["name"] for r in runs if r.get("conclusion") == "failure"],
                "pending": [r["name"] for r in runs
                            if r.get("status") in ("queued", "in_progress")],
            }

    reviewed_sha, rverr = latest_bot_review(repo, pr)
    if rverr:
        rep["errors"].append(f"reviews: {rverr}")
    rep["bot_reviewed_sha"] = reviewed_sha

    check_state, check_name, check_conclusion, cerr = bot_check_state(
        repo, rep["head_sha"])
    if cerr:
        rep["errors"].append(f"bot check: {cerr}")
    rep["bot_check_state"] = check_state
    rep["bot_check_name"] = check_name
    rep["bot_check_conclusion"] = check_conclusion

    reviewed, evidence = reviewed_head_verdict(
        rep["head_sha"], reviewed_sha, check_state)
    rep["bot_reviewed_head"] = reviewed
    rep["bot_review_evidence"] = evidence
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pr", default="auto")
    ap.add_argument("--repo", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--once", action="store_true",
                    help="one-shot snapshot (default and only mode)")
    args = ap.parse_args()

    repo = args.repo or repo_of_origin()
    if not repo:
        print("could not determine repo; pass --repo owner/name", file=sys.stderr)
        sys.exit(2)

    pr, err = resolve_pr(args.pr, repo)
    if err or not pr:
        print(f"could not resolve PR: {err}", file=sys.stderr)
        sys.exit(2)

    rep = snapshot(pr, repo)

    if args.json:
        print(json.dumps(rep, indent=2))
        return

    print(f"PR #{rep['pr']}  state={rep.get('state')}  mergeable={rep.get('mergeable')}  "
          f"review={rep.get('review_decision')}")
    ci = rep.get("ci")
    if ci:
        print(f"CI: {ci['total']} checks, {len(ci['failed'])} failed, {len(ci['pending'])} pending")
    for c in rep["bot_comments"]:
        print(f"  [{c['severity']}] {c['path']}:{c['line']}  {c['excerpt']}")
        print(f"       {c['url']}")
    if not rep["bot_comments"]:
        print("review-bot comments: none unresolved")
    # Readiness verdict: a clean snapshot is only trustworthy once the bot has
    # reviewed the CURRENT head. Otherwise CI is green but the bot is still
    # thinking (it lags CI by minutes) and its findings land after 'ready'.
    evidence = rep.get("bot_review_evidence")
    if rep.get("bot_reviewed_head") is True:
        print(f"bot reviewed: current head ({str(rep.get('head_sha'))[:8]}) "
              f"via {evidence} — snapshot complete")
    elif evidence == "check-pending":
        print(f"bot reviewed: NOT YET — {rep.get('bot_check_name') or 'bot check'} "
              f"still running on {str(rep.get('head_sha'))[:8]}; WAIT, do not call it ready")
    elif evidence == "check-aborted":
        print(f"bot reviewed: NOT CERTIFIED — {rep.get('bot_check_name') or 'bot check'} "
              f"ended {rep.get('bot_check_conclusion') or 'without a conclusion'} on "
              f"{str(rep.get('head_sha'))[:8]}; WAIT, do not call it ready")
    elif rep.get("bot_reviewed_head") is False:
        print(f"bot reviewed: STALE — last reviewed {str(rep.get('bot_reviewed_sha'))[:8]}, "
              f"head is {str(rep.get('head_sha'))[:8]}; WAIT, do not call it ready")
    else:
        print("bot reviewed: unknown (no bot review or check on this head) "
              "— do not call it ready")
    if rep["resolved_bot_comments"]:
        print(f"({len(rep['resolved_bot_comments'])} bot comments already resolved, not blocking)")
    for e in rep["errors"]:
        print(f"  ! {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
