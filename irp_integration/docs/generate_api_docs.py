"""
Generate ``docs/api.md`` from the package's source docstrings and type hints.

Uses pdoc's introspection and Google-style docstring conversion to walk
``irp_integration`` and its submodules and emit a single Markdown API reference.
Docstrings and signatures are the single source of truth, so the generated
reference cannot drift from the code.

Usage:
    python docs/generate_api_docs.py

Requires the ``dev`` extra (``pip install irp-integration[dev]``), which provides
pdoc.
"""

import importlib
import pkgutil
import re
import warnings
from pathlib import Path

import pdoc.doc
import pdoc.docstrings

PACKAGE = "irp_integration"

# Module order for the reference. Any submodule not listed here is appended
# afterwards in import order, so new modules still appear without edits.
MODULE_ORDER = [
    "client",
    "edm",
    "portfolio",
    "mri_import",
    "treaty",
    "analysis",
    "grouping",
    "analysis_validation",
    "rdm",
    "risk_data_job",
    "import_job",
    "export_job",
    "s3",
    "reference_data",
    "databridge",
    "exceptions",
    "validators",
    "utils",
    "constants",
]


def discover_modules() -> list:
    """Return submodule short names in MODULE_ORDER, then any leftovers."""
    pkg = importlib.import_module(PACKAGE)
    found = [mi.name for mi in pkgutil.iter_modules(pkg.__path__) if not mi.name.startswith("_")]
    ordered = []
    for name in MODULE_ORDER:
        if name in found and name not in ordered:
            ordered.append(name)
    for name in found:
        if name not in ordered:
            ordered.append(name)
    return ordered


def is_public(name: str) -> bool:
    return not name.startswith("_") or name == "__init__"


# --- docstring rendering -----------------------------------------------------


def _leading(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _dedent(lines: list) -> list:
    indents = [_leading(l) for l in lines if l.strip()]
    cut = min(indents) if indents else 0
    return [l[cut:] if l.strip() else "" for l in lines]


def _render_bullets(lines: list) -> str:
    items = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith(("- ", "* ")):
            items.append(s[2:].strip())
        elif items:
            items[-1] += " " + s
        else:
            items.append(s)
    return "\n".join(f"- {it}" for it in items)


def render_prose(text: str) -> str:
    """Render a free-form (module/class) docstring as readable Markdown.

    Handles paragraphs, ``Label:``-introduced indented sub-blocks, and bullet
    lists with wrapped continuation lines.
    """
    if not text.strip():
        return ""
    return _render_lines(text.split("\n")).strip()


def _render_lines(lines: list) -> str:
    out = []
    i, n = 0, len(lines)
    while i < n:
        if not lines[i].strip():
            i += 1
            continue
        indent = _leading(lines[i])
        stripped = lines[i].strip()

        j = i + 1
        while j < n and not lines[j].strip():
            j += 1
        next_indent = _leading(lines[j]) if j < n else -1

        if stripped.endswith(":") and next_indent > indent:
            out.append(f"**{stripped}**")
            out.append("")
            sub = []
            i = j
            while i < n and (not lines[i].strip() or _leading(lines[i]) > indent):
                sub.append(lines[i])
                i += 1
            out.append(_render_lines(_dedent(sub)))
            out.append("")
        elif stripped.startswith(("- ", "* ")):
            block = []
            while i < n and (not lines[i].strip() or _leading(lines[i]) >= indent):
                block.append(lines[i])
                i += 1
            out.append(_render_bullets(_dedent(block)))
            out.append("")
        else:
            para = []
            while (
                i < n
                and lines[i].strip()
                and _leading(lines[i]) == indent
                and not lines[i].strip().startswith(("- ", "* "))
            ):
                para.append(lines[i].strip())
                i += 1
            out.append(" ".join(para))
            out.append("")
    return "\n".join(out).strip()


def render_method_doc(text: str) -> str:
    """Render a Google-style method docstring as Markdown via pdoc."""
    if not text.strip():
        return ""
    md = pdoc.docstrings.google(text)
    # pdoc emits ``###### Section:`` headings; bold them instead so they don't
    # crowd the heading hierarchy or leak into anchors.
    md = re.sub(r"^#{4,6}\s*(.+?)\s*$", r"**\1**", md, flags=re.MULTILINE)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


# --- structure rendering -----------------------------------------------------


def _normalize_types(text: str) -> str:
    """Collapse version-dependent type paths to a stable public form.

    pandas and numpy expose their public types (e.g. ``DataFrame``, ``Series``,
    ``ndarray``) under private internal modules (``pandas.core.*``,
    ``numpy.core``/``numpy._core``) that move between releases, so pdoc renders
    the annotation differently depending on the installed version. Collapse those
    internal paths to the public ``pandas.<Name>`` / ``numpy.<Name>`` form so the
    generated docs don't drift with the version present at generation time.

    The dev extra also pins pandas/numpy; this normalization is the second line
    of defence so the committed docs stay reproducible even if the pins change.
    """
    text = re.sub(r"\bpandas\.core\.[\w.]+\.(\w+)", r"pandas.\1", text)
    text = re.sub(r"\bnumpy\.(?:_?core)\.[\w.]+\.(\w+)", r"numpy.\1", text)
    return text


def render_signature(func) -> str:
    return _normalize_types(f"{func.funcdef} {func.name}{func.signature}")


def render_function(func, level: int) -> list:
    out = [f"{'#' * level} `{func.name}`", "", "```python", render_signature(func), "```", ""]
    body = render_method_doc(func.docstring)
    if body:
        out.append(body)
        out.append("")
    return out


def render_class(cls, anchors, toc: list) -> list:
    heading = f"class {cls.name}"
    anchor = anchors.make(heading)
    toc.append(f"  - [{cls.name}](#{anchor})")

    out = [f"### `{heading}`", ""]
    if cls.bases:
        out.append(f"*Bases:* `{', '.join(b[2] for b in cls.bases)}`")
        out.append("")
    body = render_prose(cls.docstring)
    if body:
        out.append(body)
        out.append("")

    methods = [d for d in cls.own_members if d.kind == "function" and is_public(d.name)]
    ctor = [m for m in methods if m.name == "__init__"]
    others = [m for m in methods if m.name != "__init__"]
    for func in ctor + others:
        out.extend(render_function(func, level=4))
    return out


def render_module(short_name: str, anchors, toc: list) -> list:
    full_name = f"{PACKAGE}.{short_name}"
    mod = pdoc.doc.Module.from_name(full_name)

    anchor = anchors.make(full_name)
    toc.append(f"- [`{full_name}`](#{anchor})")

    out = [f"## `{full_name}`", ""]
    body = render_prose(mod.docstring)
    if body:
        out.append(body)
        out.append("")

    classes = [d for d in mod.own_members if d.kind == "class" and is_public(d.name)]
    functions = [d for d in mod.own_members if d.kind == "function" and is_public(d.name)]

    for cls in classes:
        out.extend(render_class(cls, anchors, toc))

    if functions:
        out.append("### Functions")
        out.append("")
        for func in functions:
            out.extend(render_function(func, level=4))

    out.append("---")
    out.append("")
    return out


class AnchorRegistry:
    """Builds GitHub-compatible heading anchors with de-duplication."""

    def __init__(self) -> None:
        self._seen = {}

    def make(self, heading_text: str) -> str:
        slug = re.sub(r"[^\w\- ]+", "", heading_text.lower())
        slug = re.sub(r"-+", "-", slug.replace(" ", "-")).strip("-")
        count = self._seen.get(slug, 0)
        self._seen[slug] = count + 1
        return slug if count == 0 else f"{slug}-{count}"


def main() -> None:
    # The lazy manager properties use string forward refs that pdoc cannot
    # resolve at module scope; the resulting warnings are expected noise.
    warnings.filterwarnings("ignore", category=UserWarning, module="pdoc")

    anchors = AnchorRegistry()
    pkg_doc = pdoc.doc.Module.from_name(PACKAGE)

    toc, body = [], []
    for short_name in discover_modules():
        body.extend(render_module(short_name, anchors, toc))

    header = [
        "# API Reference",
        "",
        "_This file is generated from source docstrings by "
        "`docs/generate_api_docs.py`. Do not edit by hand — run "
        "`python docs/generate_api_docs.py` to regenerate._",
        "",
    ]
    intro = render_prose(pkg_doc.docstring)
    if intro:
        header.append(intro)
        header.append("")
    header.append("## Table of Contents")
    header.append("")
    header.extend(toc)
    header.append("")
    header.append("---")
    header.append("")

    output = "\n".join(header + body).rstrip() + "\n"
    out_path = Path(__file__).resolve().parent / "api.md"
    out_path.write_text(output, encoding="utf-8")
    print(f"Wrote {out_path} ({len(output.splitlines())} lines)")


if __name__ == "__main__":
    main()
