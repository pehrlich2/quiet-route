#!/usr/bin/env bash
# Build the static site and force-push it to the gh-pages branch of the "origin" remote.
# Private pages (see PRIVATE in build_static.py) are never included.
set -euo pipefail
cd "$(dirname "$0")/.."
origin="$(git remote get-url origin)"
uv run python scripts/build_static.py
cd dist
rm -rf .git
git init -q -b gh-pages
git add -A
git commit -q -m "Publish site $(date +%Y-%m-%d)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -q -f "$origin" gh-pages
echo "pushed dist/ to gh-pages on $origin"
