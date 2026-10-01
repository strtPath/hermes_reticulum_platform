#!/usr/bin/env python3
"""Read-only PR snapshot for the babysit loop (docs/skills/babysit-pr-for-review-bot.md).

Reports, for a PR:
  - unresolved review-bot comments (inline review comments, grouped by severity badge)
  - CI check status
  - mergeability

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


def snapshot(pr, repo):
    rep = {"pr": pr, "repo": repo, "bot_comments": [], "resolved_bot_comments": [],
           "ci": None, "mergeable": None,
           "state": None, "review_decision": None, "errors": []}

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
        checks, cerr = gh_json(["api", f"repos/{repo}/commits/{sha_out.strip()}/check-runs"])
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
    if rep["resolved_bot_comments"]:
        print(f"({len(rep['resolved_bot_comments'])} bot comments already resolved, not blocking)")
    for e in rep["errors"]:
        print(f"  ! {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
