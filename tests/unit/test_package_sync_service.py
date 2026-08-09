"""Default member-name derivation for package members.

``_default_name`` strips the directory and the trailing extension from a
selected exposure file path (Windows or POSIX).
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize("filename, expected", [
    ("PORTFOLIO.BAK", "PORTFOLIO"),        # trailing extension dropped
    ("acme_re-2024.mdf", "acme_re-2024"),  # underscores/hyphens preserved
    ("no_extension", "no_extension"),      # nothing to strip
    ("multi.part.name.bak", "multi.part.name"),  # only the final extension goes
])
def test_default_name_strips_trailing_extension(filename, expected):
    from app.routers.packages import _default_name
    assert _default_name(f"C:\\share\\{filename}") == expected   # Windows path
    assert _default_name(f"/mnt/share/{filename}") == expected    # POSIX path
