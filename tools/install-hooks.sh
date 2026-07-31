#!/usr/bin/env bash
# Copy the tracked hooks into .git/hooks (which git never clones). Run once per
# checkout; re-run after tools/pre-commit changes.
set -eu
root="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
install -m 755 "$root/tools/pre-commit" "$root/.git/hooks/pre-commit"
echo "installed .git/hooks/pre-commit"
