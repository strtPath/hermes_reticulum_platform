#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# update-adapter.sh — pull the latest adapter code, then say exactly
# what a Hermes gateway restart does and whether anything else is needed.
#
# Usage: bash scripts/update-adapter.sh [--branch NAME]
#
# Why a restart is the other half of the update: the Hermes gateway imports
# the adapter out of this checkout when it starts (the plugin shim in
# ~/.hermes/plugins/reticulum points sys.path at this repo's src/). A pull
# here changes what the *next* gateway start loads; the running gateway keeps
# using the code already in memory until you restart it. Restart now or later
# — this script only needs to tell you which extra steps the diff demands.
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

BRANCH=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --branch)
            BRANCH="$2"
            shift 2
            ;;
        --help|-h)
            sed -n '2,14p' "${BASH_SOURCE[0]}"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

current="$(git rev-parse --abbrev-ref HEAD)"
if [ "$current" = "HEAD" ]; then
    echo "This checkout is detached. Switch to a real branch first (git switch dev)."
    exit 1
fi
if [ -n "$BRANCH" ] && [ "$BRANCH" != "$current" ]; then
    echo "This checkout is on '$current', not '$BRANCH'."
    echo "The gateway runs whatever branch is checked out here, so switch first:"
    echo "  git switch $BRANCH"
    exit 1
fi
if ! git rev-parse --abbrev-ref "$current@{upstream}" >/dev/null 2>&1; then
    echo "'$current' has no upstream; refusing to pull into it."
    echo "For the adapter, the integration branch is dev: git switch dev"
    exit 1
fi
upstream="$(git rev-parse --abbrev-ref "$current@{upstream}")"

if [ -n "$(git status --porcelain)" ]; then
    echo "Note: you have uncommitted local edits. A fast-forward leaves them"
    echo "      alone unless an incoming file collides with one."
fi

old="$(git rev-parse HEAD)"
upstream_remote="$(git config --get "branch.$current.remote")" || upstream_remote=""
if [ -z "$upstream_remote" ]; then
    echo "Could not resolve the remote for '$current'; refusing to fetch blindly."
    exit 1
fi
git fetch "$upstream_remote" --prune

if ! git merge --ff-only "$upstream"; then
    if git merge-base --is-ancestor HEAD "$upstream"; then
        echo "Fast-forward blocked: your local edits collide with incoming files."
    else
        echo "Not a fast-forward: '$current' has commits its upstream does not have."
    fi
    echo "Nothing was changed. Stash/rebase by hand, then re-run this script."
    exit 1
fi
new="$(git rev-parse HEAD)"

moved() {
    git diff --name-only "$old..$new" -- "$@"
}

if [ "$old" = "$new" ]; then
    echo "Already up to date: '$current' is at $(git log -1 --format='%h — %s')."
    echo "No new commits were pulled. If a previous run's restart is still"
    echo "pending, it is still owed — the gateway keeps its loaded code until:"
    echo "  hermes gateway restart"
    exit 0
fi

echo "Updated '$current': $(git rev-list --count "$old..$new") new commit(s)."
git log --oneline "$old..$new" | sed 's/^/  /'
echo

needs_restart=0

# Everything under src/hermes_reticulum the gateway imports: plugin, core,
# utils, top-level modules. bridge/ is the deprecated standalone (the gateway
# never imports it) and mesh-tool-gate/ has its own bucket because it is a
# copied plugin, not shim-resolved code.
adapter_changes="$(moved src/hermes_reticulum \
    ':(exclude)src/hermes_reticulum/bridge' \
    ':(exclude)src/hermes_reticulum/mesh-tool-gate')"
if [ -n "$adapter_changes" ]; then
    echo "Adapter code changed. It is imported live from this checkout, so a"
    echo "gateway restart applies it — no reinstall, no re-copy."
    needs_restart=1
fi

if [ -n "$(moved pyproject.toml requirements.txt)" ]; then
    # Name the venv the gateway actually uses: venv_path.txt (written by
    # install.sh) lists roots; whichever has bin/pip is the installed venv.
    venv_hint=""
    vp="$HOME/.hermes/plugins/reticulum/venv_path.txt"
    if [ -f "$vp" ]; then
        while IFS= read -r root; do
            if [ -x "$root/bin/pip" ]; then venv_hint="$root"; break; fi
        done < "$vp"
    fi
    if [ -n "$venv_hint" ]; then
        echo "Dependencies changed. Sync the venv the gateway actually uses"
        echo "(read from ~/.hermes/plugins/reticulum/venv_path.txt) BEFORE restarting:"
        echo "  $venv_hint/bin/pip install -e ."
    else
        echo "Dependencies changed. Sync the venv you installed with BEFORE"
        echo "restarting (default install: venv/; --venv PATH: that path;"
        echo "--global: re-run install.sh --global):"
        echo "  venv/bin/pip install -e ."
    fi
    needs_restart=1
fi

if [ -n "$(moved plugins/reticulum)" ]; then
    echo "The plugin shim itself changed (rare). Re-copy it, then restart:"
    echo "  rm -rf ~/.hermes/plugins/reticulum"
    echo "  cp -r plugins/reticulum ~/.hermes/plugins/reticulum"
    echo "  (then re-run install.sh or rewrite venv_path.txt if it was dropped)"
    needs_restart=1
fi

if [ -n "$(moved src/hermes_reticulum/mesh-tool-gate)" ]; then
    echo "The mesh-tool-gate plugin changed. Unlike the adapter, it IS a copied"
    echo "plugin — re-copy and clear stale bytecode, then restart:"
    echo "  rm -rf ~/.hermes/plugins/mesh-tool-gate"
    echo "  cp -r src/hermes_reticulum/mesh-tool-gate ~/.hermes/plugins/mesh-tool-gate"
    needs_restart=1
fi

if [ "$needs_restart" -eq 0 ]; then
    echo "Nothing the gateway imports changed — no restart needed."
    exit 0
fi

echo
echo "The running gateway still uses the code loaded when it last started."
echo "Restart when it suits you — now or later, the steps above stay valid:"
echo "  hermes gateway restart"
