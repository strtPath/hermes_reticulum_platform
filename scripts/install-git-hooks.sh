#!/bin/sh
# Point git at the committed hook directory so the PII sweep runs on every push.
# Run once per clone: scripts/install-git-hooks.sh
set -eu

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"

git config core.hooksPath .githooks
chmod +x .githooks/pre-push 2>/dev/null || true

echo "core.hooksPath -> .githooks (pre-push runs scripts/security-sweep.py)"
echo "Verify with: git config --get core.hooksPath"
