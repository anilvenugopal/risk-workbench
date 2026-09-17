"""Interactive user provisioning CLI for Risk Workbench.

Actions:
  provision   Assign a role to a newly signed-in OIDC user (access-pending)
  create      Create a new user (OIDC pre-provisioned or password account)
  reset       Reset the password for a password-auth user

Usage:
    uv run python infra/scripts/user_setup.py

Environment: reads from infra/.env (same as other infra scripts).
Requires: InquirerPy, rich  (uv sync --group dev)
"""
from __future__ import annotations

import pathlib
import sys
import urllib.parse

# ── env loading ──────────────────────────────────────────────────────────────
_ROOT = pathlib.Path(__file__).parent.parent.parent
try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / "infra" / ".env")
except ImportError:
    pass

try:
    from InquirerPy import inquirer
except ImportError:
    sys.exit("InquirerPy not installed — run: uv sync --group dev")

try:
    import bcrypt
except ImportError:
    sys.exit("bcrypt not installed — run: uv sync")

from rich.console import Console
from rich.table import Table

console = Console()


# ── database connection ───────────────────────────────────────────────────────

def _engine():
    import os
    from sqlalchemy import create_engine
    server   = os.environ.get("MSSQL_WORKBENCH_SERVER", "localhost")
    port     = os.environ.get("MSSQL_WORKBENCH_PORT", "1433")
    user     = os.environ.get("MSSQL_WORKBENCH_USER", "sa")
    password = os.environ["MSSQL_WORKBENCH_PASSWORD"]
    database = os.environ.get("MSSQL_WORKBENCH_DATABASE", "rwb_workbench")
    driver   = os.environ.get("MSSQL_DRIVER", "ODBC Driver 18 for SQL Server")
    trust    = os.environ.get("MSSQL_TRUST_CERT", "yes")
    odbc = (
        f"DRIVER={{{driver}}};SERVER={server},{port};DATABASE={database};"
        f"UID={user};PWD={password};TrustServerCertificate={trust};"
    )
    url = "mssql+pyodbc:///?odbc_connect=" + urllib.parse.quote_plus(odbc)
    return create_engine(url, pool_pre_ping=True)


def _rows(conn, sql: str, **params) -> list[dict]:
    from sqlalchemy import text
    result = conn.execute(text(sql), params)
    keys = result.keys()
    return [dict(zip(keys, row)) for row in result.fetchall()]


def _run(conn, sql: str, **params) -> None:
    from sqlalchemy import text
    conn.execute(text(sql), params)


def _hash(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=12)).decode()


def _validate_password(plain: str) -> list[str]:
    errors = []
    if len(plain) < 12:
        errors.append("Minimum 12 characters.")
    if not any(c.isupper() for c in plain):
        errors.append("Must contain an uppercase letter.")
    if not any(c.islower() for c in plain):
        errors.append("Must contain a lowercase letter.")
    if not any(c.isdigit() for c in plain):
        errors.append("Must contain a number.")
    return errors


# ── action: provision (assign role to pending OIDC user) ─────────────────────

def provision_action(engine) -> None:
    """Select an unroled (access-pending) OIDC user and assign them a role."""
    with engine.connect() as conn:
        pending = _rows(conn, """
            SELECT u.id, u.email, u.display_name, u.entra_oid
            FROM app_user u
            WHERE u.is_active = 1
              AND u.entra_oid IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM user_role ur WHERE ur.user_id = u.id
              )
            ORDER BY u.email
        """)

        if not pending:
            console.print("[dim]No OIDC users are waiting for role assignment.[/]")
            return

        choices = [
            {"name": f"{u['email']:40}  {u['display_name'] or '(no name)'}",
             "value": str(u["id"])}
            for u in pending
        ] + [{"name": "← back", "value": "back"}]

        user_id = inquirer.select(
            message=f"provision — {len(pending)} user(s) pending role assignment",
            choices=choices,
        ).execute()
        if user_id == "back":
            return

        user = next(u for u in pending if str(u["id"]) == user_id)

        roles = _roles(conn)
        role_code = inquirer.select(
            message=f"assign role to {user['email']}",
            choices=[{"name": f"{r['code']:12} {r['label']}", "value": r["code"]} for r in roles]
                  + [{"name": "← back", "value": "back"}],
        ).execute()
        if role_code == "back":
            return

        with engine.begin() as w:
            _run(w, """
                IF NOT EXISTS (
                    SELECT 1 FROM user_role WHERE user_id = :uid AND role_code = :role
                )
                INSERT INTO user_role (user_id, role_code, inserted_by)
                VALUES (:uid, :role, :uid)
            """, uid=user_id, role=role_code)

        console.print(f"[green]✓[/] {user['email']} assigned role [bold]{role_code}[/]")


# ── action: create ────────────────────────────────────────────────────────────

def create_action(engine) -> None:
    """Create a new user — OIDC pre-provisioned or password account."""
    auth_type = inquirer.select(
        message="create — account type",
        choices=[
            {"name": "oidc      Microsoft Entra / OIDC  (no password, email must match Entra)",
             "value": "oidc"},
            {"name": "password  Local password account   (admin sets temp password)",
             "value": "password"},
            {"name": "← back", "value": "back"},
        ],
    ).execute()
    if auth_type == "back":
        return

    email = inquirer.text(message="Email address:").execute().strip().lower()
    if not email:
        console.print("[red]Email is required.[/]")
        return

    display_name = inquirer.text(message="Display name:").execute().strip()
    if not display_name:
        console.print("[red]Display name is required.[/]")
        return

    with engine.connect() as conn:
        # Check for duplicate email
        existing = _rows(conn, "SELECT id FROM app_user WHERE LOWER(email) = :email", email=email)
        if existing:
            console.print(f"[red]A user with email {email} already exists.[/]")
            return

        roles = _roles(conn)

    role_code = inquirer.select(
        message="assign role",
        choices=[{"name": f"{r['code']:12} {r['label']}", "value": r["code"]} for r in roles]
              + [{"name": "none (access-pending)", "value": "none"}],
    ).execute()

    pw_hash = None
    if auth_type == "password":
        while True:
            password = inquirer.secret(message="Initial password:").execute()
            errors = _validate_password(password)
            if errors:
                for e in errors:
                    console.print(f"  [red]✗[/] {e}")
                continue
            confirm = inquirer.secret(message="Confirm password:").execute()
            if password != confirm:
                console.print("  [red]✗[/] Passwords do not match.")
                continue
            pw_hash = _hash(password)
            break

    with engine.begin() as conn:
        _run(conn, """
            INSERT INTO app_user (email, display_name, password_hash, must_change_password, is_active)
            VALUES (:email, :name, :pw, :mcp, 1)
        """, email=email, name=display_name, pw=pw_hash,
             mcp=1 if auth_type == "password" else 0)

        row = _rows(conn, "SELECT id FROM app_user WHERE LOWER(email) = :email", email=email)
        user_id = str(row[0]["id"])

        if role_code != "none":
            _run(conn, """
                INSERT INTO user_role (user_id, role_code, inserted_by)
                VALUES (:uid, :role, :uid)
            """, uid=user_id, role=role_code)

    auth_note = "password account — user must change password on first login" if auth_type == "password" \
        else "OIDC account — Entra OID will be linked on first sign-in"
    console.print(f"[green]✓[/] Created [bold]{email}[/]  role=[bold]{role_code}[/]")
    console.print(f"  [dim]{auth_note}[/]")


# ── action: reset password ────────────────────────────────────────────────────

def reset_action(engine) -> None:
    """Reset the password for a password-auth user."""
    with engine.connect() as conn:
        users = _rows(conn, """
            SELECT u.id, u.email, u.display_name,
                   STRING_AGG(rk.code, ', ') WITHIN GROUP (ORDER BY rk.sort_order) AS roles
            FROM app_user u
            LEFT JOIN user_role ur ON ur.user_id = u.id
            LEFT JOIN role_kind rk ON rk.code = ur.role_code
            WHERE u.password_hash IS NOT NULL
              AND u.is_active = 1
              AND u.entra_oid IS NULL
            GROUP BY u.id, u.email, u.display_name
            ORDER BY u.email
        """)

    if not users:
        console.print("[dim]No password-auth users found.[/]")
        return

    choices = [
        {"name": f"{u['email']:40}  roles: {u['roles'] or 'none'}",
         "value": str(u["id"])}
        for u in users
    ] + [{"name": "← back", "value": "back"}]

    user_id = inquirer.select(
        message="reset password — select user",
        choices=choices,
    ).execute()
    if user_id == "back":
        return

    user = next(u for u in users if str(u["id"]) == user_id)

    while True:
        password = inquirer.secret(message=f"New password for {user['email']}:").execute()
        errors = _validate_password(password)
        if errors:
            for e in errors:
                console.print(f"  [red]✗[/] {e}")
            continue
        confirm = inquirer.secret(message="Confirm password:").execute()
        if password != confirm:
            console.print("  [red]✗[/] Passwords do not match.")
            continue
        break

    pw_hash = _hash(password)
    with engine.begin() as conn:
        _run(conn,
            "UPDATE app_user SET password_hash = :pw, must_change_password = 1, "
            "updated_at = GETUTCDATE() WHERE id = :id",
            pw=pw_hash, id=user_id)

    console.print(f"[green]✓[/] Password reset for [bold]{user['email']}[/]  (must change on next login)")


# ── action: list users ────────────────────────────────────────────────────────

def list_action(engine) -> None:
    """Display all users in a summary table."""
    with engine.connect() as conn:
        users = _rows(conn, """
            SELECT u.email, u.display_name,
                   CASE WHEN u.entra_oid IS NOT NULL THEN 'oidc' ELSE 'password' END AS auth,
                   CASE WHEN u.is_active = 1 THEN 'active' ELSE 'inactive' END AS status,
                   STRING_AGG(rk.code, ', ') WITHIN GROUP (ORDER BY rk.sort_order) AS roles
            FROM app_user u
            LEFT JOIN user_role ur ON ur.user_id = u.id
            LEFT JOIN role_kind rk ON rk.code = ur.role_code
            GROUP BY u.email, u.display_name, u.entra_oid, u.is_active
            ORDER BY u.email
        """)

    t = Table(box=None, show_header=True, header_style="bold dim")
    t.add_column("Email")
    t.add_column("Name")
    t.add_column("Auth", justify="center")
    t.add_column("Status", justify="center")
    t.add_column("Roles")
    for u in users:
        status_style = "green" if u["status"] == "active" else "red"
        t.add_row(
            u["email"],
            u["display_name"] or "—",
            u["auth"],
            f"[{status_style}]{u['status']}[/]",
            u["roles"] or "[dim]none[/]",
        )
    console.print(t)


# ── helpers ───────────────────────────────────────────────────────────────────

def _roles(conn) -> list[dict]:
    return _rows(conn, "SELECT code, label FROM role_kind ORDER BY sort_order")


# ── menu ──────────────────────────────────────────────────────────────────────

ACTIONS = [
    ("provision", "Assign role to a newly signed-in OIDC user (access-pending)", provision_action),
    ("create",    "Create a new user (OIDC pre-provisioned or password account)", create_action),
    ("reset",     "Reset password for a password-auth user",                      reset_action),
    ("list",      "Show all users",                                               list_action),
]


def menu() -> None:
    engine = _engine()
    choices = [
        {"name": f"{name:12} {desc}", "value": name}
        for name, desc, _ in ACTIONS
    ] + [{"name": "quit", "value": "quit"}]

    _by_name = {name: fn for name, _, fn in ACTIONS}

    while True:
        pick = inquirer.select(
            message="risk-workbench · user setup",
            choices=choices,
        ).execute()
        if pick == "quit":
            break
        try:
            _by_name[pick](engine)
        except KeyboardInterrupt:
            console.print()
        except Exception as exc:
            console.print(f"[red]Error:[/] {exc}")

    engine.dispose()


if __name__ == "__main__":
    menu()
