# Contract: HTMX Partial Responses

**Date**: 2026-07-01  
**Feature**: [../spec.md](../spec.md)

This contract defines the patterns route handlers MUST follow for HTMX compatibility.

---

## Full-page vs partial detection

Detect HTMX requests via the `HX-Request: true` request header:

```python
is_htmx = request.headers.get("HX-Request") == "true"
```

---

## Response patterns

### 1 — Top-level navigation (hx-boost)

`hx-boost` intercepts `<a>` clicks and `<form>` submits on top-level nav links. The browser sends a standard GET with `HX-Request: true` and `HX-Boosted: true`. The server returns a full `<html>` document; HTMX swaps only the `<body>`.

- `HX-Push-URL` header: set to the canonical URL so the address bar updates
- No special partial needed; return the full shell template

### 2 — Content fragment swaps (sidebar links)

Sidebar links use `hx-get` + `hx-target="#main-content"` + `hx-push-url="true"`. The server returns a partial template (no `<html>/<head>/<body>` wrapper):

- Template convention: `pages/<section>.html` for full-page vs `pages/<section>_partial.html` for fragment
- Handler must check `is_htmx` and return the appropriate template

### 3 — Form submissions (login, change-password, admin)

On validation error: re-render the form partial with inline error messages. Use `HX-Retarget` + `HX-Reswap` if the error needs to land outside the default swap target.

On success: return `HX-Redirect` to the new URL (full-page redirect via HTMX):

```python
return Response(
    status_code=200,
    headers={"HX-Redirect": redirect_url},
)
```

Never return a 302 for HTMX form success — HTMX would try to swap the redirect target's content.

### 4 — Session expiry (all HTMX requests)

See [session-cookie.md](session-cookie.md) — return 200 + `HX-Redirect: /auth/login`.

### 5 — 404 / 500 on HTMX requests

- 404: return a fragment-safe partial (`templates/base/error_partial.html`) with just the error message block — swapped into `#main-content`
- 500: same; log server-side; never expose traceback

---

## Shell layout targets

```html
<!-- Main content swap target -->
<main id="main-content" ...>...</main>

<!-- Sidebar swap target (for section switches) -->
<nav id="sidebar" ...>...</nav>

<!-- Status bar (updated by some responses) -->
<footer id="status-bar" ...>...</footer>
```

Route handlers that update the sidebar and main content simultaneously may use `HX-Retarget` or `hx-select-oob` to swap multiple targets in a single response.

---

## JS-disabled fallback

All nav links MUST work as plain `<a href="...">` and `<form method="post">`. With JS disabled:

- `hx-boost` is inactive; full-page GET navigation works via standard browser mechanics
- `hx-get` sidebar links are plain `<a>` tags; clicking them performs a full-page load
- Form submits are standard `<form method="post">` — same route handlers, same templates
- The only degradation: no partial-swap smoothness; every click is a full-page load
