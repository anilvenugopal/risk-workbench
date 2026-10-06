#!/usr/bin/env bash
# rhel9-pull-code.sh — get the application code into APP_DIR, correctly
# handling both "never cloned here before" and "already a checkout, needs
# updating." Refuses by default if it finds local changes that a pull
# could disturb — see the three modes explained below.
#
# Files listed as gitignored (infra/.env, logs, .venv, etc.) are NEVER
# shown as untracked by plain "git status" — confirmed directly: only
# "git status --ignored" surfaces those, which this script does not use.
# So .env and similar are simply invisible to this script's checks; they
# are never at risk and never need special-casing here.
#
# Usage:
#   APP_DIR=/rms \
#   REPO_URL=https://github.com/anilvenugopal/risk-workbench.git \
#   BRANCH=main \
#   ./rhel9-pull-code.sh [--stash | --force]
#
#   (no flag)  — default. If local changes exist, report them and stop.
#   --stash    — safely set aside modified tracked files (git stash),
#                pull, and tell you how to get them back afterward.
#   --force    — DISCARDS modified tracked files permanently. Only use
#                this when you are certain nothing local is worth keeping.

set -euo pipefail

APP_DIR="${APP_DIR:?set APP_DIR, e.g. /rms}"
REPO_URL="${REPO_URL:-https://github.com/anilvenugopal/risk-workbench.git}"
BRANCH="${BRANCH:-main}"
MODE="${1:-}"

if [ "$MODE" != "" ] && [ "$MODE" != "--stash" ] && [ "$MODE" != "--force" ]; then
    echo "ERROR: unrecognized option '$MODE'. Use --stash, --force, or no" >&2
    echo "       option at all (default: report and stop on local changes)." >&2
    exit 1
fi

# Case 1: APP_DIR doesn't exist as a git checkout yet — first time ever.
# "-d" checks it's a folder at all; the second check specifically looks for
# the ".git" folder, which is what makes a directory an actual git
# repository rather than just a folder that happens to have files in it.
if [ ! -d "$APP_DIR/.git" ]; then
    echo "=== No existing checkout at $APP_DIR — cloning fresh ==="
    # If APP_DIR exists but isn't a git repo yet (e.g. just created and
    # chowned by rhel9-setup.sh), git clone needs the directory to either
    # not exist or be empty — this handles both.
    git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
    echo "Cloned $REPO_URL (branch: $BRANCH) into $APP_DIR."
    exit 0
fi

# Case 2: it's already a checkout. Everything below works FROM INSIDE it.
cd "$APP_DIR"

echo "=== Checking for local changes before pulling ==="
# "git status --short" prints one line per changed file:
#   " M path"  = a tracked file has been modified since the last commit
#   "?? path"  = a file exists here that git doesn't track at all
# Gitignored files (infra/.env, .venv/, logs) never appear in either list —
# confirmed directly, not assumed; see the file header above.
MODIFIED="$(git status --short | grep '^ M' || true)"
UNTRACKED="$(git status --short | grep '^??' || true)"

if [ -n "$MODIFIED" ] || [ -n "$UNTRACKED" ]; then
    if [ -n "$MODIFIED" ]; then
        echo ""
        echo "MODIFIED tracked files (changed since the last commit):"
        echo "$MODIFIED"
        echo "  These files differ from what's in git. Pulling could conflict"
        echo "  with these changes, or silently combine them with incoming"
        echo "  code in a confusing way."
    fi
    if [ -n "$UNTRACKED" ]; then
        echo ""
        echo "UNTRACKED files (not part of git at all):"
        echo "$UNTRACKED"
        echo "  These are harmless to a pull — git ignores them entirely —"
        echo "  but they are NOT part of the code and will not be updated,"
        echo "  removed, or version-controlled by anything this script does."
    fi

    if [ "$MODE" = "" ]; then
        echo ""
        echo "Stopped — no changes made. Local changes exist (see above)."
        echo "Choose one of:"
        echo "  1. Handle it yourself first: commit, discard, or move the"
        echo "     listed files elsewhere, then re-run this script plain."
        echo "  2. Re-run with --stash to safely set modified tracked files"
        echo "     aside (recoverable later with 'git stash pop'), then pull."
        echo "  3. Re-run with --force to PERMANENTLY DISCARD modified"
        echo "     tracked files and pull anyway. Untracked files are never"
        echo "     touched by --force either — only tracked, modified ones."
        exit 1
    elif [ "$MODE" = "--stash" ]; then
        if [ -n "$MODIFIED" ]; then
            echo ""
            echo "--stash: setting modified tracked files aside..."
            git stash push -m "rhel9-pull-code.sh auto-stash before pull"
            echo "Stashed. Recover later with: git stash pop"
        fi
    elif [ "$MODE" = "--force" ]; then
        if [ -n "$MODIFIED" ]; then
            echo ""
            echo "--force: PERMANENTLY DISCARDING modified tracked files..."
            git checkout -- .
        fi
    fi
else
    echo "  No local changes — clean to pull."
fi

echo ""
echo "=== Fetching and updating to $BRANCH ==="
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull origin "$BRANCH"
echo "Now on $BRANCH, up to date with origin."
