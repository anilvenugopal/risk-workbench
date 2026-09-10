"""Jinja search paths.

``app/static`` is searched second so ``{% include "icons/home.svg" %}`` in
``base/shell.html`` reaches the SVGs nginx also serves at ``/static/icons/``.
The icons stay in one directory and Jinja needs no symlink to reach them —
git checks a symlink out as a text file on Windows unless the developer turns
on Developer Mode, which broke every page render in the unit tier.
"""

TEMPLATE_DIRS = ("app/templates", "app/static")
