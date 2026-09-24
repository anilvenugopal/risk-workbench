#!/usr/bin/env bash
# sweep-export-archives.sh — delete loss-result export archives older than a
# given age, one {export_id}/{irp_analysis_id} directory at a time, and remove
# the {export_id} directories left empty behind them.
#
# The Workbench never deletes an archive (spec 014 T-15, FR-024): the stage
# worker downloads it and leaves it. This script is where the retention rule
# lives — run by hand, or from cron on the host that mounts EXPORT_ARCHIVE_DIR.
#
# Deleting an archive breaks nothing. Both readers check the file on disk
# rather than trusting the manifest's zip_file column: _archive_path
# (app/workers/export_jobs.py) downloads again, and retry_decision
# (app/services/export_service.py) falls back to requesting a new Risk Modeler
# export. What a deletion does cost is the only surviving copy of the
# uncorrected ExpValue and StdDev of every row the load procedure corrected —
# usp_load_elt_result overwrites those values in stage.rwb_loss_result_elt_data
# and keeps only a flag saying it did. Choose the age from how long CIC must be
# able to read the original Risk Modeler numbers back.
#
# Reports and deletes nothing unless --delete is given.
#
# Usage:
#   bash infra/scripts/rhel9/sweep-export-archives.sh                        # report, 90 days
#   bash infra/scripts/rhel9/sweep-export-archives.sh --older-than 30
#   bash infra/scripts/rhel9/sweep-export-archives.sh --older-than 90 --delete
#   bash infra/scripts/rhel9/sweep-export-archives.sh --dir "$EXPORT_STAGING_DIR" --older-than 7 --delete
#
# The last form clears what the stage worker cannot: a stage that fails and is
# never retried keeps its whole extracted tree, since only the next attempt on
# that analysis would remove it. A stage that finishes removes its own
# directories.
#
# Cron on RHEL9, daily at 02:00, deleting archives past 90 days:
#   0 2 * * * EXPORT_ARCHIVE_DIR=/mnt/lossshare/export_archive bash \
#       /rms/infra/scripts/rhel9/sweep-export-archives.sh --older-than 90 --delete

set -uo pipefail
# No "-e": the summary and exit code have to report a directory the share
# refused to delete, not abandon the rest of the sweep at the first one.

DIR="${EXPORT_ARCHIVE_DIR:-}"
DAYS=90
DELETE=0

usage() {
    echo "Usage: sweep-export-archives.sh [--dir PATH] [--older-than DAYS] [--delete]" >&2
    echo "  --dir          archive root (default: \$EXPORT_ARCHIVE_DIR)" >&2
    echo "  --older-than   whole days, by directory mtime (default: 90)" >&2
    echo "  --delete       actually remove; without it the script only reports" >&2
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dir) DIR="${2:-}"; shift 2 ;;
        --older-than) DAYS="${2:-}"; shift 2 ;;
        --delete) DELETE=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
    esac
done

if [ -z "$DIR" ]; then
    echo "ERROR: no archive root — set EXPORT_ARCHIVE_DIR or pass --dir." >&2
    exit 1
fi
if [ ! -d "$DIR" ]; then
    echo "ERROR: $DIR is not a directory. If that is the shared drive, check the mount before sweeping." >&2
    exit 1
fi
case "$DAYS" in
    ''|*[!0-9]*) echo "ERROR: --older-than takes whole days, got '$DAYS'." >&2; exit 1 ;;
esac

count=0
total_kb=0
failed=0

while IFS= read -r -d '' archive_dir; do
    kb="$(du -sk "$archive_dir" 2>/dev/null | cut -f1)"
    : "${kb:=0}"
    if [ "$DELETE" -eq 1 ]; then
        if ! rm -rf "$archive_dir"; then
            echo "ERROR: could not delete $archive_dir" >&2
            failed=$((failed + 1))
            continue
        fi
        echo "deleted       $archive_dir (${kb} KB)"
    else
        echo "would delete  $archive_dir (${kb} KB)"
    fi
    count=$((count + 1))
    total_kb=$((total_kb + kb))
done < <(find "$DIR" -mindepth 2 -maxdepth 2 -type d -mtime +"$DAYS" -print0 | sort -z)

# The {export_id} directories, which the stage worker creates but never removes.
# Counted after the age pass, so an export whose last analysis just went is
# included.
if [ "$DELETE" -eq 1 ]; then
    emptied="$(find "$DIR" -mindepth 1 -maxdepth 1 -type d -empty -print -delete | wc -l | tr -d ' ')"
else
    emptied="$(find "$DIR" -mindepth 1 -maxdepth 1 -type d -empty -print | wc -l | tr -d ' ')"
fi

if [ "$total_kb" -lt 1024 ]; then
    size="${total_kb} KB"
else
    size="$((total_kb / 1024)) MB"
fi
if [ "$DELETE" -eq 1 ]; then
    echo "$DIR: deleted $count directories older than $DAYS days ($size) and $emptied empty export directories."
else
    echo "$DIR: $count directories older than $DAYS days ($size), $emptied already empty. Re-run with --delete to remove them."
fi

[ "$failed" -eq 0 ] || exit 1
