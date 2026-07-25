#!/usr/bin/env bash
# Publish the staged wiki (docs/wiki/*.md) to the GitHub Wiki.
#
# PREREQUISITE (one-time, manual): the GitHub Wiki must be initialized first.
# GitHub does not provision the wiki git remote until the first page exists and
# there is no API to create it. Go to
#   https://github.com/JustChr/BavarianData/wiki
# click "Create the first page", save anything, then run this script.
#
# It clones the wiki repo, syncs the staged pages (everything in docs/wiki
# except this repo-facing README.md), commits and pushes.
set -euo pipefail

REPO="JustChr/BavarianData"
WIKI_URL="https://github.com/${REPO}.wiki.git"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${ROOT}/docs/wiki"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Cloning ${WIKI_URL} ..."
if ! git clone "$WIKI_URL" "$TMP/wiki" 2>/dev/null; then
  echo "ERROR: could not clone the wiki. Has it been initialized?"
  echo "Create the first page at https://github.com/${REPO}/wiki then re-run."
  exit 1
fi

echo "Syncing pages from ${SRC} ..."
for f in "$SRC"/*.md; do
  base="$(basename "$f")"
  [ "$base" = "README.md" ] && continue   # repo-facing note, not a wiki page
  cp "$f" "$TMP/wiki/"
done

cd "$TMP/wiki"
if git diff --quiet && git diff --cached --quiet; then
  echo "No changes to publish."
  exit 0
fi
git add -A
git commit -q -m "Sync wiki from docs/wiki"
git push
echo "Published. See https://github.com/${REPO}/wiki"
