from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_cli():
    path = Path(__file__).parents[2] / "infra" / "scripts" / "user_setup.py"
    spec = importlib.util.spec_from_file_location("user_setup_cli", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_create_password_user_calls_service(monkeypatch):
    cli = _load_cli()
    answers = iter(["password", "admin@example.com", "Admin User", "1"])
    passwords = iter(["Temporary123", "Temporary123"])
    calls = []
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(cli, "getpass", lambda prompt="": next(passwords))
    monkeypatch.setattr(
        cli.user_admin_service,
        "list_roles",
        lambda: [{"code": "admin", "label": "Administrator"}],
    )
    monkeypatch.setattr(
        cli.user_admin_service,
        "create_user",
        lambda *args: calls.append(args),
    )

    cli.create_user()

    assert calls == [
        ("admin@example.com", "Admin User", "admin", "Temporary123")
    ]


def test_menu_quits_without_database_access(monkeypatch):
    cli = _load_cli()
    monkeypatch.setattr("builtins.input", lambda prompt="": "q")

    cli.menu()
