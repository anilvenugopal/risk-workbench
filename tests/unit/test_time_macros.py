from datetime import datetime

from jinja2 import Environment, FileSystemLoader


def _local_time(ts) -> str:
    env = Environment(loader=FileSystemLoader("app/templates"))
    return str(env.get_template("partials/time_macros.html").module.local_time(ts))


def test_empty_value_renders_a_dash():
    assert _local_time(None) == '<span class="na">&mdash;</span>'


def test_timestamp_keeps_the_raw_value_and_shows_seconds():
    ts = datetime(2026, 7, 24, 18, 3, 11, 482910)
    assert _local_time(ts) == (
        '<time data-utc="2026-07-24 18:03:11.482910"'
        ' data-value="2026-07-24 18:03:11.482910"'
        ' title="2026-07-24 18:03:11.482910 UTC">2026-07-24 18:03:11</time>')
