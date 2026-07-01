"""IRP job poller — standalone process, never imported by the web layer.

Polls non-terminal irp_job rows using single-status-check get_* methods
(NEVER poll_*_to_completion, which blocks for minutes).

Run:
    python -m app.poller.run --loop --interval 30   (from start-all.sh)
    python -m app.poller.run                         (single pass, for testing)
"""

from __future__ import annotations

import argparse
import logging
import time

logger = logging.getLogger(__name__)


def poll_once() -> None:
    """Single polling pass — query pending irp_job rows, check status, update."""
    # TODO (Iteration N): implement actual IRP status checks using get_* methods.
    logger.debug("poll_once: no-op (not yet implemented)")


def main() -> None:
    parser = argparse.ArgumentParser(description="IRP job status poller")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=30, help="Seconds between passes")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger.info("Poller started (loop=%s interval=%ds)", args.loop, args.interval)

    if args.loop:
        while True:
            try:
                poll_once()
            except Exception:
                logger.exception("Unhandled error in poll_once")
            time.sleep(args.interval)
    else:
        poll_once()
        logger.info("Poller: single pass complete")


if __name__ == "__main__":
    main()
