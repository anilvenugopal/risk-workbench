"""app/services/client_service.py — the repository client list, fail-open
(spec 017 T-06, FR-008, FR-009). ``loss_clients`` is the SQLite LOSS mirror
with ``dbo.Client`` seeded; ``no_loss_db`` registers no LOSS engine at all."""

from __future__ import annotations

from app.services import client_service


def test_list_clients_reads_dbo_client_in_name_order(loss_clients):
    clients = client_service.list_clients()
    assert [(c.id, c.name) for c in clients] == [
        (27, "Travelers Corporate Cat"), (41, "Zephyr Re")]


def test_client_names_reads_only_the_asked_ids(loss_clients):
    assert client_service.client_names([41, 41, 99]) == {41: "Zephyr Re"}
    assert client_service.client_names([]) == {}


def test_unreachable_repository_fails_open(no_loss_db):
    assert client_service.list_clients() is None
    assert client_service.client_names([27]) == {}


def test_display_names_the_client_or_says_the_name_is_unavailable():
    assert client_service.display(27, "Travelers Corporate Cat") == (
        "27 - Travelers Corporate Cat")
    assert client_service.display(27, None) == "27 (name unavailable)"
    assert client_service.display(None, None) is None
