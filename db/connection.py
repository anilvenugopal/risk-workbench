"""Engine + connection management — the single pooled access layer shared by
every target and both auth modes.

We use **SQLAlchemy Core purely as a connection pool and execution surface** —
no ORM, no models. One pooled `Engine` is cached per (connection, database).
For Windows-auth connections the Kerberos ticket is ensured before the engine is
used and on every new physical connection (so long-lived pools self-renew).

Both the safe bound-parameter path (`db.execute`) and the trusted-script path
(`db.scripts`) draw their connections from here, so connection handling, auth,
and pooling live in exactly one place.
"""

import os
import logging
from contextlib import contextmanager
from typing import Dict, Optional, Tuple

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from .config import get_connection_config, build_sqlalchemy_url
from .errors import SQLServerConnectionError
from .kerberos import ensure_valid_kerberos_ticket

logger = logging.getLogger(__name__)

# Cache of live engines, keyed by (CONNECTION_NAME, database-or-empty).
_ENGINES: Dict[Tuple[str, str], Engine] = {}
# Test/override hook: pre-registered engines bypass real engine creation.
_ENGINE_OVERRIDES: Dict[Tuple[str, str], Engine] = {}


def _pool_kwargs() -> dict:
    return {
        "pool_size": int(os.getenv("MSSQL_POOL_SIZE", "5")),
        "max_overflow": int(os.getenv("MSSQL_POOL_MAX_OVERFLOW", "5")),
        "pool_timeout": int(os.getenv("MSSQL_POOL_TIMEOUT", "30")),
        "pool_recycle": int(os.getenv("MSSQL_POOL_RECYCLE", "1800")),
        "pool_pre_ping": True,
    }


def register_engine(connection_name: str, engine: Engine, database: Optional[str] = None) -> None:
    """Register a pre-built engine for a connection name (used by tests to inject
    a sqlite engine, or to supply a custom engine). Overrides real creation."""
    _ENGINE_OVERRIDES[(connection_name.upper(), database or "")] = engine


def get_engine(connection_name: str, database: Optional[str] = None) -> Engine:
    """Return the pooled engine for a target, creating and caching it on first use."""
    key = (connection_name.upper(), database or "")
    if key in _ENGINE_OVERRIDES:
        return _ENGINE_OVERRIDES[key]
    if key in _ENGINES:
        eng = _ENGINES[key]
    else:
        config = get_connection_config(connection_name)
        if config["auth_type"] == "WINDOWS" and not ensure_valid_kerberos_ticket():
            raise SQLServerConnectionError(
                f"Connection '{config['name']}' uses Windows auth but no valid "
                f"Kerberos ticket could be obtained (check KERBEROS_* env)."
            )
        url = build_sqlalchemy_url(config, database=database)
        eng = create_engine(url, **_pool_kwargs())

        # Self-renew Kerberos on each new physical connection for WINDOWS targets.
        if config["auth_type"] == "WINDOWS":
            @event.listens_for(eng, "do_connect")
            def _ensure_ticket(dialect, conn_rec, cargs, cparams):  # noqa: ANN001
                ensure_valid_kerberos_ticket()
                return None  # let the default connect proceed

        _ENGINES[key] = eng
        logger.info("Created pooled engine for %s%s", config["name"],
                    f"/{database}" if database else "")
    return eng


@contextmanager
def get_connection(connection_name: str, database: Optional[str] = None):
    """Context manager yielding a pooled SQLAlchemy Connection.

    The connection is returned to the pool on exit. Use this when you need a raw
    connection; most callers should use `db.execute` (safe path) or `db.scripts`
    (trusted-script path) instead.
    """
    engine = get_engine(connection_name, database=database)
    conn = None
    try:
        conn = engine.connect()
        yield conn
    except Exception as e:  # surface connection failures with context
        if conn is None:
            raise SQLServerConnectionError(
                f"Failed to connect (connection: {connection_name}): {e}"
            ) from e
        raise
    finally:
        if conn is not None:
            conn.close()


def test_connection(connection_name: str, database: Optional[str] = None) -> bool:
    """Lightweight connectivity probe: SELECT 1. Returns True/False, never raises."""
    from sqlalchemy import text
    try:
        with get_connection(connection_name, database=database) as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:  # noqa: BLE001 - probe
        logger.warning("test_connection(%s) failed: %s", connection_name, e)
        return False


def dispose_all() -> None:
    """Dispose every pooled engine (call on app/worker shutdown)."""
    for eng in _ENGINES.values():
        eng.dispose()
    _ENGINES.clear()


__all__ = [
    "get_engine",
    "get_connection",
    "test_connection",
    "register_engine",
    "dispose_all",
]
