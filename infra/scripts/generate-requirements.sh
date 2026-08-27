#!/usr/bin/env bash
# generate-requirements.sh — regenerate requirements.txt from uv.lock.
#
# requirements.txt is what lets the RHEL9 production server install this
# project's Python dependencies WITHOUT uv installed on the server itself
# (see docs/RHEL9_SYSTEM_SETUP.md's "uv is not part of this document"
# section for why uv is kept off the server). uv itself is only ever used
# HERE, on a developer's machine or in CI where it's already available, to
# produce this one plain file.
#
# Two ways to run this:
#
#   Generate mode (default) — regenerate the real file in place, then
#   review and commit it yourself:
#     bash infra/scripts/generate-requirements.sh
#
#   CI check mode — verify the committed requirements.txt still matches
#   what pyproject.toml/uv.lock would produce right now, WITHOUT touching
#   the real file. Fails loudly (exit code 1) if someone changed
#   dependencies without regenerating the file:
#     bash infra/scripts/generate-requirements.sh --check

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# "$1" is the first argument typed after the script's name on the command
# line — e.g. in "generate-requirements.sh --check", "$1" is "--check".
# ":-" gives it an empty-string default so this comparison doesn't error out
# if no argument was given at all (developer mode).
MODE="${1:-}"

# uv export reads uv.lock (the exact, already-resolved dependency versions
# this project committed to) and writes them out in the plain format pip
# understands — no uv needed to READ this output later, only to CREATE it.
# --frozen means "use uv.lock exactly as it is, don't re-resolve or update
# anything." --no-dev excludes developer-only tools (test runners, etc.) —
# matching what a real deployment actually needs, not what a developer's
# own machine needs.
GENERATED="$(uv export --frozen --no-dev)"

if [ "$MODE" = "--check" ]; then
    echo "=== Checking requirements.txt is up to date ==="
    if [ ! -f requirements.txt ]; then
        echo "ERROR: requirements.txt does not exist. Run this script without" >&2
        echo "       --check to generate it, then commit the result." >&2
        exit 1
    fi
    # "diff" compares two pieces of text and shows exactly what's different
    # between them, line by line. "<(...)" is bash's way of treating a
    # command's output as if it were a temporary file, so diff can compare
    # our freshly-generated text directly against the real committed file,
    # without ever writing the fresh version to disk in check mode.
    if diff -u requirements.txt <(echo "$GENERATED") > /tmp/requirements-diff.txt; then
        echo "OK — requirements.txt matches pyproject.toml/uv.lock."
    else
        echo "ERROR: requirements.txt is OUT OF DATE." >&2
        echo "       pyproject.toml or uv.lock changed without regenerating it." >&2
        echo "       Run 'bash infra/scripts/generate-requirements.sh' (no --check)" >&2
        echo "       and commit the result. Diff:" >&2
        cat /tmp/requirements-diff.txt >&2
        exit 1
    fi
else
    echo "=== Regenerating requirements.txt ==="
    echo "$GENERATED" > requirements.txt
    echo "Wrote requirements.txt. Review with 'git diff requirements.txt'"
    echo "and commit it — this is what the RHEL9 server installs from,"
    echo "with no uv involved on that side."
fi
