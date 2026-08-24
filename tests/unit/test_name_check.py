"""Unit tests for app/services/name_check.py (issues #17 + #11).

The shared Risk Modeler name-collision check: a hit blocks the caller's save,
an unreachable gateway fails OPEN (``checked=False``), and successful results are
cached in-process for a short TTL so as-you-type checks don't hammer RM and the
save-time check rides the same cache. Failures are never cached.

Uses the fake IRP gateway only — no DB fixture needed.
"""

from __future__ import annotations

from app.config import settings
from app.services import name_check


def test_collision_and_clean_shapes(fake_irp):
    fake_irp.add_edm_name("Dupe")
    hit = name_check.check_edm_name("Dupe")
    assert hit.collides and hit.names == ("Dupe",) and hit.checked
    clean = name_check.check_edm_name("Fresh")
    assert not clean.collides and clean.names == () and clean.checked


def test_blank_name_short_circuits(fake_irp):
    assert not name_check.check_edm_name("").collides
    assert not name_check.check_edm_name("   ").collides
    assert fake_irp.search_calls == []  # no gateway call for a blank name


def test_gateway_failure_fails_open(fake_irp):
    fake_irp.raise_on_search = True
    down = name_check.check_edm_name("Anything")
    assert not down.collides and down.checked is False  # never raises


def test_cache_dedupes_identical_checks(fake_irp):
    fake_irp.add_edm_name("Dupe")
    first = name_check.check_edm_name("Dupe")
    second = name_check.check_edm_name("Dupe")
    assert first.names == second.names == ("Dupe",)
    assert len(fake_irp.search_calls) == 1  # second check was a cache hit
    name_check.check_edm_name("Other")
    assert len(fake_irp.search_calls) == 2  # different name → new lookup


def test_edm_and_rdm_caches_are_distinct(fake_irp):
    name_check.check_edm_name("Same")
    name_check.check_rdm_name("Same")
    assert fake_irp.search_calls == [("edm", "Same"), ("rdm", "Same")]


def test_failure_is_not_cached(fake_irp):
    fake_irp.add_edm_name("Dupe")
    fake_irp.raise_on_search = True
    assert name_check.check_edm_name("Dupe").checked is False
    fake_irp.raise_on_search = False
    recovered = name_check.check_edm_name("Dupe")  # re-probes, not served stale
    assert recovered.collides and recovered.checked
    assert len(fake_irp.search_calls) == 2


def test_ttl_expiry_requeries(fake_irp, monkeypatch):
    monkeypatch.setattr(settings, "name_check_cache_ttl_secs", 0)
    name_check.check_edm_name("Fresh")
    name_check.check_edm_name("Fresh")  # entry already expired → gateway again
    assert len(fake_irp.search_calls) == 2


def test_cache_bound_evicts_and_never_grows_past_max(fake_irp, monkeypatch):
    monkeypatch.setattr(name_check, "_MAX_ENTRIES", 2)
    for name in ("A", "B", "C", "D"):
        name_check.check_edm_name(name)
    assert len(name_check._cache) <= 2  # bound holds (soonest-to-expire evicted)
    # ... and expired entries are preferred for eviction over live ones.
    monkeypatch.setattr(settings, "name_check_cache_ttl_secs", 0)
    name_check.clear_cache()
    name_check.check_edm_name("expired-1")   # TTL 0 → instantly stale
    monkeypatch.setattr(settings, "name_check_cache_ttl_secs", 30)
    name_check.check_edm_name("live-1")      # evicts the expired entry
    name_check.check_edm_name("live-2")
    assert ("edm", "expired-1") not in name_check._cache
    assert len(name_check._cache) <= 2
