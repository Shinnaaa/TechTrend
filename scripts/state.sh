#!/usr/bin/env bash
# Run state (history/, seen_urls.json) lives on its own branch so that main holds
# only code: forks start clean, and daily bot commits stay out of main's history.
#
#   scripts/state.sh load            check the state branch out into $TECHTREND_DATA_DIR
#                                    (created empty on the first run)
#   scripts/state.sh save "message"  commit and push any changes back to it
#
# Needs GITHUB_TOKEN and GITHUB_REPOSITORY (both provided in GitHub Actions).
set -euo pipefail

BRANCH="${STATE_BRANCH:-data}"
DIR="${TECHTREND_DATA_DIR:-data}"
REMOTE="${STATE_REMOTE:-https://x-access-token:${GITHUB_TOKEN:-}@github.com/${GITHUB_REPOSITORY:-}.git}"

case "${1:-}" in
  load)
    if git ls-remote --exit-code --heads "$REMOTE" "$BRANCH" >/dev/null 2>&1; then
      git clone --quiet --depth 1 --branch "$BRANCH" "$REMOTE" "$DIR"
      echo "Loaded state from branch '$BRANCH'."
    else
      mkdir -p "$DIR"
      git -C "$DIR" init --quiet -b "$BRANCH"
      git -C "$DIR" remote add origin "$REMOTE"
      echo "No '$BRANCH' branch yet — starting with empty state."
    fi
    ;;
  save)
    cd "$DIR"
    git config user.name  "github-actions[bot]"
    git config user.email "github-actions[bot]@users.noreply.github.com"
    git add -A
    if git diff --cached --quiet; then
      echo "No state changes."
      exit 0
    fi
    git commit --quiet -m "${2:-update state}"
    git push --quiet origin "HEAD:$BRANCH"
    echo "State saved to branch '$BRANCH'."
    ;;
  *)
    echo "usage: $0 load | save \"message\"" >&2
    exit 2
    ;;
esac
