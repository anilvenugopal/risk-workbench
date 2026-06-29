"""Kerberos / Windows-authentication support.

For connections with `AUTH_TYPE=WINDOWS`, SQL Server is reached via a Kerberos
ticket rather than a username/password. This module checks and (if configured)
renews that ticket. It shells out to the standard `klist`/`kinit` tools.

Configuration (env):
    KERBEROS_ENABLED   'true' to enable ticket management   (default false)
    KRB5_PRINCIPAL     principal, e.g. svc_acct@REALM
    KRB5_KEYTAB        path to a keytab (preferred), or
    KRB5_PASSWORD      password (fallback if no keytab)

Hardened vs. the notebook original: all output goes through `logging`, never
`print`, so it is quiet and log-friendly inside a worker/service.
"""

import os
import re
import logging
import subprocess
from datetime import datetime
from typing import Any, Dict, Optional

from .errors import SQLServerConfigurationError

logger = logging.getLogger(__name__)


def kerberos_enabled() -> bool:
    return os.getenv("KERBEROS_ENABLED", "false").lower() == "true"


def check_kerberos_status() -> Dict[str, Any]:
    """Return a dict describing the current ticket: enabled/has_ticket/principal/
    expiration/error. Never raises."""
    result: Dict[str, Any] = {
        "enabled": kerberos_enabled(),
        "has_ticket": False,
        "principal": None,
        "expiration": None,
        "error": None,
    }
    if not result["enabled"]:
        result["error"] = "Kerberos not enabled (KERBEROS_ENABLED != true)"
        return result
    try:
        out = subprocess.run(["klist"], capture_output=True, text=True, timeout=10)
        if out.returncode == 0:
            m = re.search(r"Default principal:\s+(\S+)", out.stdout)
            if m:
                result["principal"] = m.group(1)
            m = re.search(r"(\d{2}/\d{2}/\d{2,4}\s+\d{2}:\d{2}:\d{2})", out.stdout)
            if m:
                result["expiration"] = m.group(1)
            result["has_ticket"] = True
        else:
            result["error"] = out.stderr.strip() or "No valid Kerberos ticket found"
    except subprocess.TimeoutExpired:
        result["error"] = "klist timed out"
    except FileNotFoundError:
        result["error"] = "klist not found (Kerberos tools not installed)"
    except Exception as e:  # noqa: BLE001 - diagnostic only
        result["error"] = str(e)
    return result


def init_kerberos(keytab_path: Optional[str] = None,
                  principal: Optional[str] = None,
                  password: Optional[str] = None) -> bool:
    """Obtain a ticket via `kinit`, using a keytab (preferred) or password.

    Returns True on success. Raises SQLServerConfigurationError if no principal
    or no auth material is available.
    """
    principal = principal or os.getenv("KRB5_PRINCIPAL")
    keytab_path = keytab_path or os.getenv("KRB5_KEYTAB")
    password = password or os.getenv("KRB5_PASSWORD")

    if not principal:
        raise SQLServerConfigurationError("KRB5_PRINCIPAL is required for Kerberos init.")

    try:
        if keytab_path and os.path.exists(keytab_path):
            cmd = ["kinit", "-kt", keytab_path, principal]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        elif password:
            proc = subprocess.run(["kinit", principal], input=password + "\n",
                                  capture_output=True, text=True, timeout=30)
        else:
            raise SQLServerConfigurationError(
                "No Kerberos credentials: set KRB5_KEYTAB (preferred) or KRB5_PASSWORD."
            )
        if proc.returncode == 0:
            logger.info("Kerberos ticket obtained for %s", principal)
            return True
        logger.error("kinit failed: %s", (proc.stderr or "").strip())
        return False
    except subprocess.TimeoutExpired:
        logger.error("kinit timed out")
        return False
    except FileNotFoundError:
        logger.error("kinit not found (Kerberos tools not installed)")
        return False


def is_ticket_valid(min_remaining_minutes: int = 5) -> bool:
    """True if a ticket exists and has at least `min_remaining_minutes` left."""
    status = check_kerberos_status()
    if not status["has_ticket"] or not status["expiration"]:
        return False
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%y %H:%M:%S"):
        try:
            expiry = datetime.strptime(status["expiration"], fmt)
            break
        except ValueError:
            expiry = None
    if expiry is None:
        logger.warning("Could not parse Kerberos expiry '%s'", status["expiration"])
        return False
    remaining_min = (expiry - datetime.now()).total_seconds() / 60
    return remaining_min >= min_remaining_minutes


def ensure_valid_kerberos_ticket(min_remaining_minutes: int = 5) -> bool:
    """Ensure a valid ticket exists, renewing if needed. Returns True if, after
    this call, a usable ticket is present (or Kerberos is disabled = nothing to do)."""
    if not kerberos_enabled():
        return True
    if is_ticket_valid(min_remaining_minutes):
        return True
    logger.info("Kerberos ticket missing/expiring; attempting renewal")
    try:
        return init_kerberos()
    except SQLServerConfigurationError as e:
        logger.error("Cannot renew Kerberos ticket: %s", e)
        return False


__all__ = [
    "kerberos_enabled",
    "check_kerberos_status",
    "init_kerberos",
    "is_ticket_valid",
    "ensure_valid_kerberos_ticket",
]
