#!/usr/bin/env python3
"""
worker_heartbeat_demo.py — a tiny, runnable demonstration of the rwb_job
heartbeat pattern (CR-001 §5.3a): a worker doing a long BLOCKING operation
while a separate daemon thread keeps its heartbeat alive.

Stdlib only. State lives in a SQLite file so heartbeats persist to a child
table (rwb_job_heartbeat) exactly like the real design, and so a simulated
crash in one process can be detected by `reconcile` in another.

The point it proves: the work thread blocks in ONE long call (here, a sleep —
"a wait for all its worth"), yet the heartbeat keeps ticking, because the
heartbeat runs on its own thread. If the process dies, the heartbeat stops and
the reconciler can tell the job was abandoned — without any timeout tied to how
long the job "should" take.

Commands
--------
  reset                          wipe and recreate the demo database
  run     [--work-secs N]        happy path: claim -> blocking work -> complete
          [--interval S] [--fail-after S]
  crash   [--work-secs N]        simulate the worker DYING mid-work (heartbeat
          [--crash-after S]      stops abruptly, job left 'running')
  reconcile [--stale S] [--reset]  find 'running' jobs with a stale heartbeat
  status                         show all jobs and how old their heartbeat is

Try this
--------
  python3 worker_heartbeat_demo.py reset
  python3 worker_heartbeat_demo.py run   --work-secs 8  --interval 1
  python3 worker_heartbeat_demo.py crash --work-secs 30 --interval 1 --crash-after 3
  sleep 6
  python3 worker_heartbeat_demo.py reconcile --stale 4 --reset
  python3 worker_heartbeat_demo.py status
"""

import argparse
import os
import sqlite3
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

DB_DEFAULT = "hb_demo.db"
INTERVAL_DEFAULT = 2.0          # heartbeat every N seconds
STALE_DEFAULT = 6.0            # 'running' + heartbeat older than this = abandoned (~3x interval)


# ---- small helpers ---------------------------------------------------------
def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.isoformat()


def clock():
    return utcnow().strftime("%H:%M:%S")


def log(tag, msg):
    print(f"{clock()} [{tag:>4}] {msg}", flush=True)


def connect(db):
    conn = sqlite3.connect(db, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")   # lets the work + heartbeat threads write concurrently
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db):
    with connect(db) as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS rwb_job (
                id           TEXT PRIMARY KEY,
                request_key  TEXT UNIQUE NOT NULL,
                status       TEXT NOT NULL,           -- pending|running|succeeded|failed
                claimed_by   TEXT,
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS rwb_job_heartbeat (
                rwb_job_id   TEXT PRIMARY KEY REFERENCES rwb_job(id),
                worker_id    TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL
            );
            """
        )


# ---- the heartbeat writer (faithful to CR-001 §5.3a) -----------------------
class Heartbeat:
    """A separate daemon thread whose ONLY job is to stamp the job's heartbeat
    on a timer. It keeps running while the work thread is blocked, because it is
    a different thread. No lease, no ownership — just a timestamp."""

    def __init__(self, db, job_id, worker_id, interval):
        self.db = db
        self.job_id = job_id
        self.worker_id = worker_id
        self.interval = interval
        self._stop = threading.Event()   # detail #1: stoppable timer via Event.wait
        self._thread = None
        self._count = 0

    def _run(self):
        conn = connect(self.db)          # detail #4: the heartbeat thread owns its own connection
        try:
            while True:
                try:
                    self._count += 1
                    conn.execute(
                        """
                        INSERT INTO rwb_job_heartbeat (rwb_job_id, worker_id, heartbeat_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(rwb_job_id) DO UPDATE SET
                            worker_id = excluded.worker_id,
                            heartbeat_at = excluded.heartbeat_at
                        """,
                        (self.job_id, self.worker_id, iso(utcnow())),
                    )
                    conn.commit()
                    log("hb", f"stamped #{self._count}  (worker {self.worker_id}) "
                              f"— work thread is still blocked")
                except Exception as e:                       # detail #4: swallow + retry next tick
                    log("hb", f"write failed, will retry next tick: {e}")
                # Event.wait sleeps for `interval` but returns instantly when stop() is called:
                if self._stop.wait(self.interval):
                    return
        finally:
            conn.close()

    def start(self):
        self._thread = threading.Thread(target=self._run, name="heartbeat", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)


@contextmanager
def heartbeating(db, job_id, worker_id, interval):
    """detail #3: guaranteed start-before / stop-after, even if the work raises."""
    hb = Heartbeat(db, job_id, worker_id, interval)
    hb.start()
    log("work", f"heartbeat started (every {interval:g}s) — now entering the blocking call")
    try:
        yield hb
    finally:
        hb.stop()
        log("work", "heartbeat stopped")


# ---- queue operations ------------------------------------------------------
def enqueue(db, request_key=None):
    job_id = "job-" + uuid.uuid4().hex[:8]
    request_key = request_key or ("demo:" + uuid.uuid4().hex[:8])
    ts = iso(utcnow())
    with connect(db) as c:
        c.execute(
            "INSERT INTO rwb_job (id, request_key, status, created_at, updated_at) "
            "VALUES (?, ?, 'pending', ?, ?)",
            (job_id, request_key, ts, ts),
        )
    log("q", f"enqueued {job_id} (request_key={request_key}) status=pending")
    return job_id


def claim(db, job_id, worker_id):
    """Atomic pending -> running. rowcount 1 = we own it; 0 = someone else did."""
    with connect(db) as c:
        cur = c.execute(
            "UPDATE rwb_job SET status='running', claimed_by=?, updated_at=? "
            "WHERE id=? AND status='pending'",
            (worker_id, iso(utcnow()), job_id),
        )
        got = cur.rowcount == 1
    log("q", f"claim {job_id} by {worker_id}: {'ACQUIRED (pending->running)' if got else 'already taken'}")
    return got


def finish(db, job_id, status):
    with connect(db) as c:
        c.execute(
            "UPDATE rwb_job SET status=?, updated_at=?, completed_at=? WHERE id=?",
            (status, iso(utcnow()), iso(utcnow()), job_id),
        )
    log("q", f"{job_id} -> {status}")


# ---- commands --------------------------------------------------------------
def cmd_reset(args):
    if os.path.exists(args.db):
        os.remove(args.db)
    for suffix in ("-wal", "-shm"):
        p = args.db + suffix
        if os.path.exists(p):
            os.remove(p)
    init_db(args.db)
    log("db", f"reset {args.db}")


def cmd_run(args):
    init_db(args.db)
    worker_id = "w-" + uuid.uuid4().hex[:4]
    job_id = enqueue(args.db)
    if not claim(args.db, job_id, worker_id):
        return
    # --- the worker: ONE long blocking call, wrapped in the heartbeat context ---
    outcome = "succeeded"
    try:
        with heartbeating(args.db, job_id, worker_id, args.interval):
            if args.fail_after is not None:
                time.sleep(args.fail_after)
                raise RuntimeError("simulated failure mid-work")
            # A single blocking operation — "a wait for all its worth".
            time.sleep(args.work_secs)
        log("work", f"blocking operation finished after {args.work_secs:g}s")
    except Exception as e:                     # heartbeat still stops (finally) — that's detail #3
        outcome = "failed"
        log("work", f"work raised: {e}")
    finish(args.db, job_id, outcome)


def cmd_crash(args):
    init_db(args.db)
    worker_id = "w-" + uuid.uuid4().hex[:4]
    job_id = enqueue(args.db)
    if not claim(args.db, job_id, worker_id):
        return
    hb = Heartbeat(args.db, job_id, worker_id, args.interval)
    hb.start()
    log("work", f"heartbeat started; simulating a crash after {args.crash_after:g}s "
                f"of a {args.work_secs:g}s job")
    time.sleep(args.crash_after)
    log("work", "*** process dies here (os._exit) — no completion, heartbeat stops abruptly ***")
    sys.stdout.flush()
    os._exit(137)   # hard kill: no finally, no finish(); job stays 'running', heartbeat goes stale


def cmd_reconcile(args):
    init_db(args.db)
    now = utcnow()
    with connect(args.db) as c:
        rows = c.execute(
            """
            SELECT j.id, j.status, j.claimed_by, h.heartbeat_at
            FROM rwb_job j
            LEFT JOIN rwb_job_heartbeat h ON h.rwb_job_id = j.id
            WHERE j.status = 'running'
            """
        ).fetchall()
        abandoned = []
        for r in rows:
            if r["heartbeat_at"] is None:
                age = None
                stale = True
            else:
                age = (now - datetime.fromisoformat(r["heartbeat_at"])).total_seconds()
                stale = age > args.stale
            state = "STALE -> abandoned" if stale else f"fresh ({age:.1f}s old)"
            log("recon", f"{r['id']} claimed_by={r['claimed_by']} heartbeat "
                         f"{'(never)' if age is None else f'{age:.1f}s old'} -> {state}")
            if stale:
                abandoned.append(r["id"])
        if args.reset and abandoned:
            for jid in abandoned:
                c.execute(
                    "UPDATE rwb_job SET status='pending', claimed_by=NULL, updated_at=? WHERE id=? AND status='running'",
                    (iso(utcnow()), jid),
                )
            log("recon", f"re-enqueued {len(abandoned)} abandoned job(s): running -> pending "
                         f"(a worker would now pick them up again)")
        elif not rows:
            log("recon", "no 'running' jobs to check")


def cmd_status(args):
    init_db(args.db)
    now = utcnow()
    with connect(args.db) as c:
        rows = c.execute(
            """
            SELECT j.id, j.request_key, j.status, j.claimed_by, h.heartbeat_at
            FROM rwb_job j
            LEFT JOIN rwb_job_heartbeat h ON h.rwb_job_id = j.id
            ORDER BY j.created_at
            """
        ).fetchall()
    if not rows:
        log("stat", "no jobs")
        return
    for r in rows:
        if r["heartbeat_at"]:
            age = f"{(now - datetime.fromisoformat(r['heartbeat_at'])).total_seconds():.1f}s ago"
        else:
            age = "never"
        log("stat", f"{r['id']}  status={r['status']:<9} claimed_by={r['claimed_by'] or '-':<7} "
                    f"last_heartbeat={age}")


# ---- arg parsing -----------------------------------------------------------
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=DB_DEFAULT, help=f"SQLite file (default {DB_DEFAULT})")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("reset", help="wipe and recreate the demo DB")
    sp.set_defaults(func=cmd_reset)

    sp = sub.add_parser("run", help="happy path: claim -> blocking work -> complete")
    sp.add_argument("--work-secs", type=float, default=10.0)
    sp.add_argument("--interval", type=float, default=INTERVAL_DEFAULT)
    sp.add_argument("--fail-after", type=float, default=None,
                    help="raise mid-work after S seconds (shows the heartbeat still stops)")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("crash", help="simulate the worker dying mid-work")
    sp.add_argument("--work-secs", type=float, default=30.0)
    sp.add_argument("--interval", type=float, default=INTERVAL_DEFAULT)
    sp.add_argument("--crash-after", type=float, default=3.0)
    sp.set_defaults(func=cmd_crash)

    sp = sub.add_parser("reconcile", help="find running jobs with a stale heartbeat")
    sp.add_argument("--stale", type=float, default=STALE_DEFAULT,
                    help=f"heartbeat older than this = abandoned (default {STALE_DEFAULT:g}s)")
    sp.add_argument("--reset", action="store_true", help="re-enqueue abandoned jobs (running->pending)")
    sp.set_defaults(func=cmd_reconcile)

    sp = sub.add_parser("status", help="show all jobs and heartbeat ages")
    sp.set_defaults(func=cmd_status)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()