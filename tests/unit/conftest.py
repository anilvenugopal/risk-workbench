"""Unit-test-level conftest.

Sets the minimum required env vars before any app module is imported,
so that `app.config.Settings()` (which runs at module load) does not fail
with a missing-field validation error in environments without a .env file.
"""

import os

# Must be set before any app.* import triggers Settings() at module level.
os.environ.setdefault("SESSION_SECRET_KEY", "unit-test-secret-key-not-for-production")
