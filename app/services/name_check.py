"""Risk Modeler name-collision check — shared by the EDM, RDM, and breakout-group
flows (issue #17; spec 005 P-25).

The single place that answers "does this name already exist in Risk Modeler?",
distinguishing *no collision* from *check unavailable*:

  • a hit  → the save must BLOCK (``CollisionCheck.collides``);
  • the gateway can't answer → fail OPEN (``checked=False``) — the caller saves
    with a visible warning, and the worker-side submit validation (irp-integration
    ≥ 0.2.1 rejects duplicate names before upload) is the backstop.

Results are cached in-process for a short TTL (issue #11): the as-you-type check
endpoints fire once per debounced keystroke per analyst, and the save-time check
rides the same cache, so a save right after typing is usually free. The cache is
per-process (one per uvicorn worker) and best-effort by design — a stale "free"
answer only shifts the failure to the worker backstop. Failures are never cached,
so recovery after an outage is immediate. There is deliberately NO server-side
rate limiter: with auth required, a client debounce, and this cache, the RM call
rate per analyst is already bounded for a trusted-intranet deployment.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass

from app.config import settings
from app.services import irp_gateway
from app.services.errors import InvalidMemberName

logger = logging.getLogger(__name__)

_MAX_ENTRIES = 512
_NAME_MAX = 50
_NAME_RE = re.compile(r"[A-Za-z0-9_-]+")

# (kind, exposure irp id, trimmed name) -> (monotonic expiry, colliding names)
_cache: dict[tuple[str, str | None, str], tuple[float, tuple[str, ...]]] = {}
_lock = threading.Lock()


@dataclass(frozen=True)
class CollisionCheck:
    """Outcome of one name check. ``names`` non-empty ⇒ the save must block;
    ``checked=False`` ⇒ Risk Modeler was unreachable ⇒ fail open with a warning."""
    names: tuple[str, ...] = ()
    checked: bool = True

    @property
    def collides(self) -> bool:
        return bool(self.names)


def check_edm_name(name: str) -> CollisionCheck:
    return _check("edm", name)


def check_rdm_name(name: str) -> CollisionCheck:
    return _check("rdm", name)


def clean_entity_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned or len(cleaned) > _NAME_MAX or not _NAME_RE.fullmatch(cleaned):
        raise InvalidMemberName(
            "EDM/RDM names may use only letters, numbers, underscores, and "
            f"hyphens, with a maximum of {_NAME_MAX} characters.")
    return cleaned


def check_portfolio_name(*, exposure_irp_id: str, name: str) -> CollisionCheck:
    """Portfolio-name check scoped to one EDM (spec 005 P-25): a breakout
    group's name is blocked when any portfolio in the exposure already carries
    it. Cached per (exposure, name), same TTL as the EDM/RDM checks."""
    return _check("portfolio", name, exposure_irp_id=exposure_irp_id)


def clear_cache() -> None:
    """Drop every cached result (test hook)."""
    with _lock:
        _cache.clear()


def _check(kind: str, name: str, *,
           exposure_irp_id: str | None = None) -> CollisionCheck:
    trimmed = (name or "").strip()
    if not trimmed:
        return CollisionCheck()

    key = (kind, exposure_irp_id, trimmed)
    now = time.monotonic()
    with _lock:
        entry = _cache.get(key)
        if entry is not None and entry[0] > now:
            return CollisionCheck(names=entry[1])

    try:
        names = tuple(hit.name for hit in
                      _search(kind, trimmed, exposure_irp_id))
    except Exception:  # noqa: BLE001 — fail open; the worker submit is the backstop
        logger.warning("%s name-collision check unavailable (gateway error)",
                       kind.upper(), exc_info=True)
        return CollisionCheck(checked=False)

    with _lock:
        if len(_cache) >= _MAX_ENTRIES:
            _evict(now)
        _cache[key] = (now + settings.name_check_cache_ttl_secs, names)
    return CollisionCheck(names=names)


def _search(kind: str, name: str, exposure_irp_id: str | None):
    if kind == "edm":
        return irp_gateway.search_edms(name)
    if kind == "rdm":
        return irp_gateway.search_rdms(name)
    if kind == "portfolio":
        return irp_gateway.find_portfolio_by_name(
            exposure_irp_id=exposure_irp_id, name=name)
    raise ValueError(f"unknown name-check kind {kind!r}")


def _evict(now: float) -> None:
    """Drop expired entries; if none expired, drop the soonest-to-expire one."""
    expired = [k for k, (exp, _) in _cache.items() if exp <= now]
    for k in expired:
        del _cache[k]
    if not expired and _cache:
        del _cache[min(_cache, key=lambda k: _cache[k][0])]


__all__ = ["CollisionCheck", "clean_entity_name", "check_edm_name", "check_rdm_name",
           "check_portfolio_name", "clear_cache"]
