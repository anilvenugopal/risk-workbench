"""Fill the WORKBENCH database with demo submissions and contracts so the
submission list, its filters and the typeaheads have something to search, and
the LOSS database's dbo.CRMContractStatus mirror so the January bulk update
script (infra/scripts/bulk_update_contract_status.sql) has rows to read.

Development only (``APP_ENV=development``). Rows are written through
``submission_service``, so they carry the status event, the P-20 treaty year and
the P-03 expiration default the app itself writes.

Generation is deterministic: the same ``--count`` produces the same names, so
``--clear`` deletes a previous run before seeding again.

Run via the Makefile:
    make seed-demo                          # Docker
    make wsl-seed-demo                      # WSL2
    make seed-demo ARGS="--count 120 --clear"
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import bcrypt
from sqlalchemy import bindparam, text

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "db" / "bootstrap").is_dir())
sys.path.insert(0, str(ROOT))

from app.services import submission_service  # noqa: E402
from db import get_connection  # noqa: E402

SEED = 20260922

ANALYSTS = [
    ("alex.morgan@example.com", "Alex Morgan"),
    ("priya.raman@example.com", "Priya Raman"),
    ("dan.oleary@example.com", "Dan OLeary"),
]
ANALYST_PASSWORD = "Analyst1234567!"

CEDANTS = [
    "Atlantic Mutual Insurance",
    "Gulf Coast Mutual",
    "Pacific Rim Insurance Group",
    "Midwest Farmers Mutual",
    "Great Lakes Property Insurance",
    "Southeast Underwriters",
    "Cascade General Insurance",
    "Lone Star Insurance Exchange",
    "Northern Plains Mutual",
    "Bay State Insurance Company",
]

# Program label, and the treaty type a contract of that program takes.
PROGRAMS = [
    ("Property Cat", "per_occurrence_cat_xol"),
    ("Property Per Risk", "per_risk_xol"),
    ("Aggregate Cat", "aggregate_cat_xol"),
    ("Casualty Clash", "per_occurrence_xol"),
    ("Stop Loss", "stop_loss"),
    ("Reinstatement Premium", "reinstatement_premium_protection"),
    ("Top and Drop", "top_and_drop"),
    ("Second Event", "second_third_fourth_event_risk_exposed"),
]

TREATY_YEARS = [2024, 2025, 2026, 2027]
INCEPTION_MONTHS = [1, 4, 6, 7, 10]
# rwb_loss dbo.Client ids from bootstrap_loss, and "no client chosen".
CLIENT_IDS = [1, 2, 3, None]
MODELING_STATUSES = ["ACTIVE"] * 7 + ["COMPLETED"] * 2 + ["CANCELLED"]
CONTRACT_STATUSES = ["OPEN"] * 4 + ["WON"] * 4 + ["LOST"] * 2
CONTRACTS_PER_SUBMISSION = [0] + [1] * 4 + [2] * 3 + [3] * 2
# CRM's own status words (note 34 D9), keyed by the Workbench code they match.
CRM_STATUS_WORDS = {"OPEN": "Open", "WON": "Won", "LOST": "Lost"}
CRM_ONLY_ROWS = 10


@dataclass
class Plan:
    name: str
    cedant_name: str
    owner_index: int
    client_id: int | None
    treaty_year: int | None
    data_vintage: date | None
    modeling_status: str
    contracts: list[submission_service.ContractInput] = field(default_factory=list)


def _plans(count: int) -> list[Plan]:
    rng = random.Random(SEED)
    plans: list[Plan] = []
    crm_sequence = 1000
    for index in range(count):
        cedant = rng.choice(CEDANTS)
        program, treaty_type = rng.choice(PROGRAMS)
        year = rng.choice(TREATY_YEARS)
        short = " ".join(cedant.split()[:2])
        contracts: list[submission_service.ContractInput] = []
        for _ in range(rng.choice(CONTRACTS_PER_SUBMISSION)):
            crm_sequence += 1
            inception = date(year, rng.choice(INCEPTION_MONTHS), 1)
            # Most terms take the one-year default (P-03); a few run three years.
            expiration = date(year + 3, inception.month, 1) if rng.random() < 0.15 else None
            contracts.append(submission_service.ContractInput(
                crm_id=f"CRM-{year}-{crm_sequence}",
                treaty_type_code=treaty_type if not contracts else rng.choice(PROGRAMS)[1],
                inception_date=inception,
                expiration_date=expiration,
                contract_status_code=rng.choice(CONTRACT_STATUSES),
            ))
        plans.append(Plan(
            name=f"{short} {year} {program} - demo {index + 1:03d}",
            cedant_name=cedant,
            owner_index=index % len(ANALYSTS),
            client_id=rng.choice(CLIENT_IDS),
            # With no contract there is no inception to fill the treaty year from.
            treaty_year=year if not contracts else None,
            data_vintage=date(year - 1, 12, 31) if rng.random() < 0.6 else None,
            modeling_status=rng.choice(MODELING_STATUSES),
            contracts=contracts,
        ))
    return plans


def _crm_rows(plans: list[Plan]) -> tuple[list[tuple[str, str]], int]:
    """One dbo.CRMContractStatus row per seeded contract, about a third with a
    status the contract does not carry, plus CRM IDs the Workbench lacks.
    Returns the rows and how many differ from the Workbench."""
    rng = random.Random(SEED + 1)
    rows: list[tuple[str, str]] = []
    differing = 0
    for plan in plans:
        for contract in plan.contracts:
            code = contract.contract_status_code
            if rng.random() < 1 / 3:
                code = rng.choice([c for c in CRM_STATUS_WORDS if c != code])
                differing += 1
            rows.append((contract.crm_id, CRM_STATUS_WORDS[code]))
    for n in range(CRM_ONLY_ROWS):
        rows.append((f"CRM-CRMONLY-{1001 + n}", rng.choice(list(CRM_STATUS_WORDS.values()))))
    return rows, differing


def _analyst_ids() -> list[str]:
    """The three demo analysts' ids, creating any that are missing."""
    ids: list[str] = []
    with get_connection("WORKBENCH") as conn, conn.begin():
        for email, display_name in ANALYSTS:
            user_id = conn.execute(
                text("SELECT id FROM app_user WHERE email = :email"), {"email": email},
            ).scalar()
            if user_id is None:
                pw_hash = bcrypt.hashpw(ANALYST_PASSWORD.encode(), bcrypt.gensalt(rounds=12))
                conn.execute(text(
                    """
                    INSERT INTO app_user
                        (email, display_name, password_hash, must_change_password, is_active)
                    VALUES (:email, :name, :pw, 0, 1)
                    """
                ), {"email": email, "name": display_name, "pw": pw_hash.decode()})
                user_id = conn.execute(
                    text("SELECT id FROM app_user WHERE email = :email"), {"email": email},
                ).scalar()
                conn.execute(
                    text("INSERT INTO user_role (user_id, role_code) VALUES (:uid, 'analyst')"),
                    {"uid": str(user_id)})
                print(f"  [app_user] {email} created")
            ids.append(str(user_id))
    return ids


def _clear(names: list[str]) -> int:
    """Delete the submissions a previous run of the same ``--count`` wrote, with
    their contracts and status events, and every dbo.CRMContractStatus row."""
    with get_connection("LOSS") as conn, conn.begin():
        conn.execute(text("DELETE FROM dbo.CRMContractStatus"))
    deleted = 0
    with get_connection("WORKBENCH") as conn, conn.begin():
        # SQL Server binds at most 2,100 parameters per statement.
        for start in range(0, len(names), 500):
            chunk = names[start:start + 500]
            ids = [row[0] for row in conn.execute(
                text("SELECT id FROM submission WHERE name IN :names").bindparams(
                    bindparam("names", expanding=True)), {"names": chunk})]
            if not ids:
                continue
            for table in ("contract", "submission_status_event"):
                conn.execute(
                    text(f"DELETE FROM {table} WHERE submission_id IN :ids").bindparams(
                        bindparam("ids", expanding=True)), {"ids": ids})
            deleted += conn.execute(
                text("DELETE FROM submission WHERE id IN :ids").bindparams(
                    bindparam("ids", expanding=True)), {"ids": ids}).rowcount
    return deleted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed demo submissions and contracts.")
    parser.add_argument("--count", type=int, default=60,
                        help="submissions to write (default 60 - two list pages)")
    parser.add_argument("--clear", action="store_true",
                        help="delete a previous run's submissions first")
    args = parser.parse_args(argv)

    if os.environ.get("APP_ENV", "development") != "development":
        print("ERROR: demo data is development only (APP_ENV=development).", file=sys.stderr)
        return 1

    plans = _plans(args.count)
    contracts_written = 0
    try:
        if args.clear:
            print(f"Demo seed: cleared {_clear([plan.name for plan in plans])} submissions")
        owners = _analyst_ids()
        for plan in plans:
            actor = owners[plan.owner_index]
            result = submission_service.create_submission(
                name=plan.name, cedant_name=plan.cedant_name,
                treaty_year=plan.treaty_year, client_id=plan.client_id,
                data_vintage=plan.data_vintage, contracts=plan.contracts,
                actor_id=actor, confirmed=True,
            )
            contracts_written += len(plan.contracts)
            if plan.modeling_status != submission_service.ACTIVE:
                submission = submission_service.get_submission(result.submission_id)
                submission_service.set_statuses(
                    submission_id=result.submission_id,
                    modeling_status=plan.modeling_status, reason="Demo data",
                    expected_updated_at=submission.updated_at, actor_id=actor,
                )
        crm_rows, differing = _crm_rows(plans)
        with get_connection("LOSS") as conn, conn.begin():
            conn.execute(
                text("INSERT INTO dbo.CRMContractStatus (CRMID, Status) VALUES (:crm, :status)"),
                [{"crm": crm, "status": status} for crm, status in crm_rows])
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Demo seed: {len(plans)} submissions, {contracts_written} contracts, owned by "
          f"{', '.join(email for email, _ in ANALYSTS)} (password {ANALYST_PASSWORD})")
    print(f"Demo seed: {len(crm_rows)} dbo.CRMContractStatus rows, {differing} with a status "
          f"the contract does not carry, {CRM_ONLY_ROWS} CRM IDs the Workbench lacks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
