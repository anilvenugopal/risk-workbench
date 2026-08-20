"""Analysis-template and template-suite persistence."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from irp_integration.analysis_validation import (
    classify_model_profile,
    validate_analysis_settings,
)
from sqlalchemy import text

from app.services._common import _txn, _uid, _utcnow
from db import is_unique_violation


@dataclass(frozen=True)
class TemplateValues:
    name: str
    analysis_profile_name: str
    output_profile_name: str
    event_rate_scheme_name: str | None
    currency_code: str
    currency_scheme_code: str
    currency_vintage: str
    min_loss_threshold: Decimal = Decimal("1.00")
    num_max_loss_event: int = 1
    franchise_deductible: bool = False
    treat_construction_occupancy_as_unknown: bool = True


class TemplateServiceError(ValueError):
    pass


class TemplateValidationError(TemplateServiceError):
    def __init__(self, errors: Iterable[str]):
        self.errors = tuple(errors)
        super().__init__("; ".join(self.errors))


class TemplateInUseError(TemplateServiceError):
    def __init__(self, suite_names: Iterable[str]):
        self.suite_names = tuple(suite_names)
        super().__init__(
            "Template is used by: " + ", ".join(self.suite_names)
        )


def _clean_optional(value: str | None) -> str | None:
    cleaned = value.strip() if isinstance(value, str) else value
    return cleaned or None


def _template_params(values: TemplateValues) -> dict:
    return {
        "name": values.name.strip(),
        "profile": values.analysis_profile_name.strip(),
        "output": values.output_profile_name.strip(),
        "scheme": _clean_optional(values.event_rate_scheme_name),
        "currency": values.currency_code.strip(),
        "currency_scheme": values.currency_scheme_code.strip(),
        "currency_vintage": values.currency_vintage.strip(),
        "threshold": str(
            Decimal(values.min_loss_threshold).quantize(Decimal("0.01"))
        ),
        "max_events": int(values.num_max_loss_event),
        "franchise": bool(values.franchise_deductible),
        "occupancy": bool(values.treat_construction_occupancy_as_unknown),
    }


def _row(conn, sql: str, params: dict | None = None) -> dict | None:
    result = conn.execute(text(sql), params or {}).mappings().first()
    return dict(result) if result is not None else None


def _rows(conn, sql: str, params: dict | None = None) -> list[dict]:
    return [dict(row) for row in conn.execute(text(sql), params or {}).mappings()]


def _profile(conn, name: str) -> dict | None:
    return _row(
        conn,
        """
        SELECT name, is_accumulation, software_version_code, peril_code,
               model_region_code
        FROM irp_model_profile
        WHERE name = :name
        """,
        {"name": name},
    )


def profile_family(
    is_accumulation: bool | None, software_version_code: str | None,
) -> str | None:
    """DLM/HD/Accumulation marker for a cached model profile (FR-004);
    None when the cached software version is absent — no marker, and the
    DLM scheme-required rule does not apply."""
    if is_accumulation:
        return "Accumulation"
    if software_version_code is None:
        return None
    return classify_model_profile(software_version_code)


def _validate_profile_scheme_pairing(conn, params: dict) -> list[str]:
    profile = _profile(conn, params["profile"]) if params["profile"] else None
    if profile is None or profile["is_accumulation"]:
        return []
    version = profile["software_version_code"]
    if version is None:
        return []

    scheme = None
    if params["scheme"]:
        scheme = _row(
            conn,
            """
            SELECT peril_code, model_region_code
            FROM irp_event_rate_scheme
            WHERE name = :name
            """,
            {"name": params["scheme"]},
        )
    pair_known = (
        scheme is not None
        and profile["peril_code"] is not None
        and profile["model_region_code"] is not None
    )
    return validate_analysis_settings(
        software_version_code=version,
        scheme_provided=bool(params["scheme"]),
        profile_peril_code=profile["peril_code"] or "",
        profile_model_region_code=profile["model_region_code"] or "",
        scheme_peril_code=scheme["peril_code"] if pair_known else None,
        scheme_model_region_code=(
            scheme["model_region_code"] if pair_known else None
        ),
    )


def _validate_currency(conn, params: dict) -> list[str]:
    scheme_code = params["currency_scheme"]
    vintage = params["currency_vintage"]
    if not scheme_code:
        return []

    scheme = _row(
        conn, "SELECT code FROM irp_currency_scheme WHERE code = :code",
        {"code": scheme_code},
    )
    if scheme is None:
        return []  # unresolved — not a save-blocker (R9)

    has_vintages = _row(
        conn,
        "SELECT 1 AS found FROM irp_currency_scheme_vintage "
        "WHERE currency_scheme_code = :code",
        {"code": scheme_code},
    ) is not None
    if not has_vintages:
        return [f'Currency scheme "{scheme_code}" has no cached vintages']

    if not vintage:
        return []
    vintage_known = _row(
        conn, "SELECT 1 AS found FROM irp_currency_scheme_vintage WHERE vintage = :vintage",
        {"vintage": vintage},
    ) is not None
    if not vintage_known:
        return []  # unresolved — not a save-blocker (R9)

    matches = _row(
        conn,
        "SELECT 1 AS found FROM irp_currency_scheme_vintage "
        "WHERE currency_scheme_code = :code AND vintage = :vintage",
        {"code": scheme_code, "vintage": vintage},
    ) is not None
    if not matches:
        return [
            f'Currency vintage "{vintage}" does not belong to '
            f'currency scheme "{scheme_code}"'
        ]
    return []


def _validate_template(conn, params: dict) -> list[str]:
    errors = []
    for label, key in (
        ("Template name", "name"),
        ("Model profile", "profile"),
        ("Output profile", "output"),
        ("Currency", "currency"),
        ("Currency scheme", "currency_scheme"),
        ("Currency vintage", "currency_vintage"),
    ):
        if not params[key]:
            errors.append(f"{label} is required")

    errors.extend(_validate_profile_scheme_pairing(conn, params))
    errors.extend(_validate_currency(conn, params))
    return errors


def _replace_tags(conn, template_id: str, tags: Iterable[str], actor_id: str | None) -> None:
    conn.execute(
        text("DELETE FROM analysis_template_tag WHERE template_id = :id"),
        {"id": template_id},
    )
    seen: set[str] = set()
    for tag in tags:
        name = tag.strip()
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        conn.execute(
            text("""
                INSERT INTO analysis_template_tag
                    (template_id, tag_name, inserted_at, inserted_by)
                VALUES (:template_id, :tag_name, :now, :actor)
            """),
            {
                "template_id": template_id,
                "tag_name": name,
                "now": _utcnow(),
                "actor": actor_id,
            },
        )


def save_template(
    values: TemplateValues,
    *,
    tags: Iterable[str] = (),
    actor_id: str | None = None,
    template_id: str | None = None,
    conn=None,
) -> str:
    params = _template_params(values)
    with _txn(conn) as working:
        errors = _validate_template(working, params)
        if errors:
            raise TemplateValidationError(errors)

        duplicate_sql = """
            SELECT id FROM analysis_template
            WHERE LOWER(name) = LOWER(:name) AND deleted_at IS NULL
        """
        duplicate_params = {"name": params["name"]}
        if template_id is not None:
            duplicate_sql += " AND id <> :id"
            duplicate_params["id"] = template_id
        duplicate = _row(working, duplicate_sql, duplicate_params)
        if duplicate:
            raise TemplateValidationError([
                f'An analysis template named "{params["name"]}" already exists'
            ])

        now = _utcnow()
        saved_id = template_id or str(uuid.uuid4())
        write_params = {
            **params,
            "id": saved_id,
            "actor": actor_id,
            "now": now,
        }
        try:
            with working.begin_nested():
                if template_id is None:
                    working.execute(text("""
                        INSERT INTO analysis_template
                          (id, name, analysis_profile_name, output_profile_name,
                           event_rate_scheme_name, currency_code, currency_scheme_code,
                           currency_vintage, min_loss_threshold,
                           num_max_loss_event, franchise_deductible,
                           treat_construction_occupancy_as_unknown,
                           inserted_at, updated_at, inserted_by, updated_by)
                        VALUES
                          (:id, :name, :profile, :output, :scheme, :currency,
                           :currency_scheme, :currency_vintage,
                           :threshold, :max_events, :franchise, :occupancy,
                           :now, :now, :actor, :actor)
                    """), write_params)
                else:
                    result = working.execute(text("""
                        UPDATE analysis_template
                        SET name = :name,
                            analysis_profile_name = :profile,
                            output_profile_name = :output,
                            event_rate_scheme_name = :scheme,
                            currency_code = :currency,
                            currency_scheme_code = :currency_scheme,
                            currency_vintage = :currency_vintage,
                            min_loss_threshold = :threshold,
                            num_max_loss_event = :max_events,
                            franchise_deductible = :franchise,
                            treat_construction_occupancy_as_unknown = :occupancy,
                            updated_at = :now,
                            updated_by = :actor
                        WHERE id = :id AND deleted_at IS NULL
                    """), write_params)
                    if result.rowcount == 0:
                        raise TemplateServiceError("Analysis template was not found")
        except TemplateServiceError:
            raise
        except Exception as exc:
            if is_unique_violation(exc):
                raise TemplateValidationError([
                    f'An analysis template named "{params["name"]}" already exists'
                ]) from exc
            raise
        _replace_tags(working, saved_id, tags, actor_id)
        return saved_id


# Resolved flags for a template's currency scheme/vintage references (FR-011);
# expects the template table aliased as `t`.
_CURRENCY_RESOLVED_SQL = """
           CASE WHEN EXISTS (
               SELECT 1 FROM irp_currency_scheme cs
               WHERE cs.code = t.currency_scheme_code
           ) THEN 1 ELSE 0 END AS currency_scheme_resolved,
           CASE WHEN EXISTS (
               SELECT 1 FROM irp_currency_scheme_vintage v
               WHERE v.currency_scheme_code = t.currency_scheme_code
                 AND v.vintage = t.currency_vintage
           ) THEN 1 ELSE 0 END AS currency_vintage_resolved
"""


_TEMPLATE_SELECT = f"""
    SELECT t.*, u.display_name AS author_name,
           mp.id AS model_profile_id, mp.is_accumulation,
           mp.software_version_code,
           op.id AS output_profile_id,
           ers.id AS event_rate_scheme_id,
           c.id AS currency_id,
           {_CURRENCY_RESOLVED_SQL}
    FROM analysis_template t
    LEFT JOIN app_user u ON u.id = t.inserted_by
    LEFT JOIN irp_model_profile mp ON mp.name = t.analysis_profile_name
    LEFT JOIN irp_output_profile op ON op.name = t.output_profile_name
    LEFT JOIN irp_event_rate_scheme ers ON ers.name = t.event_rate_scheme_name
    LEFT JOIN irp_currency c ON c.code = t.currency_code
"""


def _decorate_template(row: dict, tags: Iterable[str] = ()) -> dict:
    row["id"] = _uid(row["id"])
    row["tags"] = list(tags)
    row["model_profile_unresolved"] = row["model_profile_id"] is None
    row["output_profile_unresolved"] = row["output_profile_id"] is None
    row["event_rate_scheme_unresolved"] = (
        bool(row["event_rate_scheme_name"])
        and row["event_rate_scheme_id"] is None
    )
    row["currency_unresolved"] = row["currency_id"] is None
    row["currency_scheme_unresolved"] = not row["currency_scheme_resolved"]
    row["currency_vintage_unresolved"] = not row["currency_vintage_resolved"]
    row["unresolved"] = any((
        row["model_profile_unresolved"],
        row["output_profile_unresolved"],
        row["event_rate_scheme_unresolved"],
        row["currency_unresolved"],
        row["currency_scheme_unresolved"],
        row["currency_vintage_unresolved"],
    ))
    row["profile_family"] = (
        None if row["model_profile_id"] is None
        else profile_family(row["is_accumulation"], row["software_version_code"])
    )
    return row


def get_template(template_id: str, *, conn=None) -> dict | None:
    with _txn(conn) as working:
        row = _row(
            working,
            _TEMPLATE_SELECT + " WHERE t.id = :id AND t.deleted_at IS NULL",
            {"id": template_id},
        )
        if row is None:
            return None
        tags = _rows(
            working,
            """
            SELECT tag_name FROM analysis_template_tag
            WHERE template_id = :id ORDER BY tag_name
            """,
            {"id": template_id},
        )
        return _decorate_template(row, (tag["tag_name"] for tag in tags))


def list_templates(*, conn=None) -> list[dict]:
    with _txn(conn) as working:
        rows = _rows(
            working,
            _TEMPLATE_SELECT + """
            WHERE t.deleted_at IS NULL
            ORDER BY t.name
            """,
        )
        tags_by_id: dict[str, list[str]] = {}
        for tag in _rows(working, """
            SELECT tt.template_id, tt.tag_name
            FROM analysis_template_tag tt
            JOIN analysis_template t ON t.id = tt.template_id
            WHERE t.deleted_at IS NULL
            ORDER BY tt.tag_name
        """):
            tags_by_id.setdefault(_uid(tag["template_id"]), []).append(tag["tag_name"])
        return [
            _decorate_template(row, tags_by_id.get(_uid(row["id"]), []))
            for row in rows
        ]


def list_tag_names(*, conn=None) -> list[str]:
    with _txn(conn) as working:
        return [row["tag_name"] for row in _rows(working, """
            SELECT DISTINCT tt.tag_name
            FROM analysis_template_tag tt
            JOIN analysis_template t ON t.id = tt.template_id
            WHERE t.deleted_at IS NULL
            ORDER BY tt.tag_name
        """)]


def delete_template(
    template_id: str, *, actor_id: str | None = None, conn=None,
) -> None:
    with _txn(conn) as working:
        suites = _rows(working, """
            SELECT DISTINCT s.name
            FROM template_suite_item i
            JOIN template_suite s ON s.id = i.suite_id
            WHERE i.template_id = :id AND s.deleted_at IS NULL
            ORDER BY s.name
        """, {"id": template_id})
        if suites:
            raise TemplateInUseError(row["name"] for row in suites)
        result = working.execute(text("""
            UPDATE analysis_template
            SET deleted_at = :now, updated_at = :now, updated_by = :actor
            WHERE id = :id AND deleted_at IS NULL
        """), {"id": template_id, "now": _utcnow(), "actor": actor_id})
        if result.rowcount == 0:
            raise TemplateServiceError("Analysis template was not found")


def _validate_suite(conn, name: str, template_ids: list[str]) -> None:
    errors = []
    if not name.strip():
        errors.append("Suite name is required")
    ids = [_uid(template_id) for template_id in template_ids]
    if len(ids) != len(set(ids)):
        errors.append("A template can appear only once in a suite")
    if ids:
        params = {f"id{index}": value for index, value in enumerate(ids)}
        marks = ", ".join(f":id{index}" for index in range(len(ids)))
        found = {
            _uid(row["id"])
            for row in _rows(conn, f"""
                SELECT id FROM analysis_template
                WHERE deleted_at IS NULL AND id IN ({marks})
            """, params)
        }
        missing = [value for value in ids if value not in found]
        if missing:
            errors.append("Every suite entry must reference a live analysis template")
    if errors:
        raise TemplateValidationError(errors)


def save_suite(
    name: str,
    template_ids: Iterable[str],
    *,
    actor_id: str | None = None,
    suite_id: str | None = None,
    conn=None,
) -> str:
    id_list = list(template_ids)
    clean_name = name.strip()
    with _txn(conn) as working:
        _validate_suite(working, clean_name, id_list)
        duplicate_sql = """
            SELECT id FROM template_suite
            WHERE LOWER(name) = LOWER(:name) AND deleted_at IS NULL
        """
        duplicate_params = {"name": clean_name}
        if suite_id is not None:
            duplicate_sql += " AND id <> :id"
            duplicate_params["id"] = suite_id
        duplicate = _row(working, duplicate_sql, duplicate_params)
        if duplicate:
            raise TemplateValidationError([
                f'A template suite named "{clean_name}" already exists'
            ])

        now = _utcnow()
        saved_id = suite_id or str(uuid.uuid4())
        params = {"id": saved_id, "name": clean_name, "now": now, "actor": actor_id}
        try:
            with working.begin_nested():
                if suite_id is None:
                    working.execute(text("""
                        INSERT INTO template_suite
                            (id, name, inserted_at, updated_at, inserted_by, updated_by)
                        VALUES (:id, :name, :now, :now, :actor, :actor)
                    """), params)
                else:
                    result = working.execute(text("""
                        UPDATE template_suite
                        SET name = :name, updated_at = :now, updated_by = :actor
                        WHERE id = :id AND deleted_at IS NULL
                    """), params)
                    if result.rowcount == 0:
                        raise TemplateServiceError("Template suite was not found")
        except TemplateServiceError:
            raise
        except Exception as exc:
            if is_unique_violation(exc):
                raise TemplateValidationError([
                    f'A template suite named "{clean_name}" already exists'
                ]) from exc
            raise

        working.execute(
            text("DELETE FROM template_suite_item WHERE suite_id = :id"),
            {"id": saved_id},
        )
        for template_id in id_list:
            working.execute(text("""
                INSERT INTO template_suite_item
                    (id, suite_id, template_id, inserted_at, inserted_by)
                VALUES (:id, :suite, :template, :now, :actor)
            """), {
                "id": str(uuid.uuid4()),
                "suite": saved_id,
                "template": template_id,
                "now": now,
                "actor": actor_id,
            })
        return saved_id


def get_suite(suite_id: str, *, conn=None) -> dict | None:
    with _txn(conn) as working:
        suite = _row(working, """
            SELECT s.*, u.display_name AS author_name
            FROM template_suite s
            LEFT JOIN app_user u ON u.id = s.inserted_by
            WHERE s.id = :id AND s.deleted_at IS NULL
        """, {"id": suite_id})
        if suite is None:
            return None
        suite["id"] = _uid(suite["id"])
        items = _rows(working, f"""
            SELECT i.id, i.template_id,
                   t.name AS template_name, t.deleted_at AS template_deleted_at,
                   mp.id AS model_profile_id, op.id AS output_profile_id,
                   ers.id AS event_rate_scheme_id, c.id AS currency_id,
                   t.event_rate_scheme_name,
                   {_CURRENCY_RESOLVED_SQL}
            FROM template_suite_item i
            LEFT JOIN analysis_template t ON t.id = i.template_id
            LEFT JOIN irp_model_profile mp ON mp.name = t.analysis_profile_name
            LEFT JOIN irp_output_profile op ON op.name = t.output_profile_name
            LEFT JOIN irp_event_rate_scheme ers ON ers.name = t.event_rate_scheme_name
            LEFT JOIN irp_currency c ON c.code = t.currency_code
            WHERE i.suite_id = :id
            ORDER BY t.name
        """, {"id": suite_id})
        for item in items:
            item["id"] = _uid(item["id"])
            item["template_id"] = _uid(item["template_id"])
            item["unresolved"] = (
                item["template_name"] is None
                or item["template_deleted_at"] is not None
                or item["model_profile_id"] is None
                or item["output_profile_id"] is None
                or item["currency_id"] is None
                or not item["currency_scheme_resolved"]
                or not item["currency_vintage_resolved"]
                or (
                    bool(item["event_rate_scheme_name"])
                    and item["event_rate_scheme_id"] is None
                )
            )
        suite["items"] = items
        suite["item_count"] = len(items)
        suite["unresolved"] = any(item["unresolved"] for item in items)
        return suite


def list_suites(*, conn=None) -> list[dict]:
    with _txn(conn) as working:
        ids = _rows(working, """
            SELECT id FROM template_suite
            WHERE deleted_at IS NULL ORDER BY name
        """)
        return [get_suite(_uid(row["id"]), conn=working) for row in ids]


def delete_suite(
    suite_id: str, *, actor_id: str | None = None, conn=None,
) -> None:
    with _txn(conn) as working:
        result = working.execute(text("""
            UPDATE template_suite
            SET deleted_at = :now, updated_at = :now, updated_by = :actor
            WHERE id = :id AND deleted_at IS NULL
        """), {"id": suite_id, "now": _utcnow(), "actor": actor_id})
        if result.rowcount == 0:
            raise TemplateServiceError("Template suite was not found")


def scheme_options(profile_name: str, *, conn=None) -> list[dict]:
    with _txn(conn) as working:
        profile = _profile(working, profile_name)
        if (
            profile is None
            or profile["peril_code"] is None
            or profile["model_region_code"] is None
        ):
            return []
        options = _rows(working, """
            SELECT name, peril_code, model_region_code, model_version_code, is_hd
            FROM irp_event_rate_scheme
            WHERE peril_code = :peril AND model_region_code = :region
            ORDER BY name
        """, {
            "peril": profile["peril_code"],
            "region": profile["model_region_code"],
        })
        selected = len(options) == 1
        for option in options:
            option["selected"] = selected
        return options


def reference_options(*, conn=None) -> dict[str, list[dict]]:
    with _txn(conn) as working:
        profiles = _rows(working, """
            SELECT name, is_accumulation, software_version_code,
                   peril_code, model_region_code
            FROM irp_model_profile ORDER BY name
        """)
        for profile in profiles:
            profile["family"] = profile_family(
                profile["is_accumulation"], profile["software_version_code"]
            )
        return {
            "model_profiles": profiles,
            "output_profiles": _rows(
                working, "SELECT name FROM irp_output_profile ORDER BY name"
            ),
            "currencies": _rows(
                working, "SELECT code, name FROM irp_currency ORDER BY code"
            ),
            "currency_schemes": _rows(
                working,
                "SELECT code, name FROM irp_currency_scheme ORDER BY name",
            ),
        }


def vintage_options(scheme_code: str, *, conn=None) -> list[dict]:
    """Cached vintages for one currency scheme, latest `effective_date` first.

    A duplicate vintage code (raw-snapshot cache, no unique key) collapses to
    its most recent `effective_date` — the select only needs one option per
    distinct vintage value. Pre-selects the latest by effective date (T-09
    default)."""
    with _txn(conn) as working:
        if not scheme_code:
            return []
        rows = _rows(working, """
            SELECT vintage, effective_date
            FROM irp_currency_scheme_vintage
            WHERE currency_scheme_code = :code
            ORDER BY effective_date DESC
        """, {"code": scheme_code})
        latest_by_vintage: dict[str, dict] = {}
        for row in rows:
            if row["vintage"] not in latest_by_vintage:
                latest_by_vintage[row["vintage"]] = row
        options = sorted(
            latest_by_vintage.values(),
            key=lambda row: row["effective_date"],
            reverse=True,
        )
        for index, option in enumerate(options):
            option["selected"] = index == 0
        return options


__all__ = [
    "TemplateInUseError",
    "TemplateServiceError",
    "TemplateValidationError",
    "TemplateValues",
    "delete_suite",
    "delete_template",
    "get_suite",
    "get_template",
    "list_suites",
    "list_tag_names",
    "list_templates",
    "profile_family",
    "reference_options",
    "save_suite",
    "save_template",
    "scheme_options",
    "vintage_options",
]
