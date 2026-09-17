"""Interactive production user setup using only runtime dependencies."""

from __future__ import annotations

from getpass import getpass

from app.auth.password import validate_password_requirements
from app.services import user_admin_service


def _choose(prompt: str, rows: list[dict], label) -> dict | None:
    if not rows:
        print("No matching users found.")
        return None
    for index, row in enumerate(rows, start=1):
        print(f"  {index}. {label(row)}")
    raw = input(f"{prompt} (blank to cancel): ").strip()
    if not raw:
        return None
    try:
        return rows[int(raw) - 1]
    except (ValueError, IndexError):
        print("Invalid selection.")
        return None


def _choose_role(*, allow_none: bool) -> str | None:
    roles = user_admin_service.list_roles()
    options = roles + ([{"code": "", "label": "No role (access pending)"}] if allow_none else [])
    selected = _choose("Role", options, lambda row: f"{row['code'] or 'none'} - {row['label']}")
    return selected["code"] if selected else None


def _read_password() -> str | None:
    password = getpass("Temporary password: ")
    errors = validate_password_requirements(password)
    if errors:
        print("Password rejected: " + "; ".join(errors))
        return None
    if password != getpass("Confirm temporary password: "):
        print("Passwords do not match.")
        return None
    return password


def create_user() -> None:
    account_type = input("Account type [password/oidc]: ").strip().lower()
    if account_type not in {"password", "oidc"}:
        print("Enter password or oidc.")
        return
    email = input("Email: ").strip()
    display_name = input("Display name: ").strip()
    if not email or not display_name:
        print("Email and display name are required.")
        return
    role_code = _choose_role(allow_none=True)
    password = _read_password() if account_type == "password" else None
    if account_type == "password" and password is None:
        return
    user_admin_service.create_user(email, display_name, role_code, password)
    suffix = " The user must change the password at first login." if password else ""
    print(f"Created {email.lower()}.{suffix}")


def provision_pending_oidc_user() -> None:
    user = _choose(
        "User",
        user_admin_service.list_pending_oidc_users(),
        lambda row: f"{row['email']} ({row['display_name'] or 'no display name'})",
    )
    if not user:
        return
    role_code = _choose_role(allow_none=False)
    if role_code:
        user_admin_service.assign_role(str(user["id"]), role_code)
        print(f"Assigned {role_code} to {user['email']}.")


def reset_password() -> None:
    user = _choose(
        "User",
        user_admin_service.list_password_users(),
        lambda row: f"{row['email']} ({row['display_name'] or 'no display name'})",
    )
    if not user:
        return
    password = _read_password()
    if password is None:
        return
    user_admin_service.reset_password(str(user["id"]), password)
    print(f"Reset password for {user['email']}. The user must change it at next login.")


def list_users() -> None:
    users = user_admin_service.list_users()
    if not users:
        print("No users found.")
        return
    for user in users:
        auth = "password" if user["password_hash"] else "oidc"
        status = "active" if user["is_active"] else "inactive"
        print(f"{user['email']} | {user['display_name'] or '-'} | {auth} | {status} | {user['roles'] or 'no role'}")


ACTIONS = {
    "1": create_user,
    "2": provision_pending_oidc_user,
    "3": reset_password,
    "4": list_users,
}


def menu() -> None:
    while True:
        print("\nRisk Workbench user setup")
        print("  1. Create password or OIDC user")
        print("  2. Assign role to pending OIDC user")
        print("  3. Reset password")
        print("  4. List users")
        print("  q. Quit")
        choice = input("Select: ").strip().lower()
        if choice in {"q", "quit"}:
            return
        action = ACTIONS.get(choice)
        if not action:
            print("Invalid selection.")
            continue
        try:
            action()
        except Exception as exc:
            print(f"Error: {exc}")


if __name__ == "__main__":
    menu()
