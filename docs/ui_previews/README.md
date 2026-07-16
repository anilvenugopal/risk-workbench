# UI Previews

Quick rendered HTML mockups, eyeballed **before** a screen gets wired in. Lightweight by
design — see [../UI_WORKFLOW.md](../UI_WORKFLOW.md).

**Only for screens with real new layout.** Skip trivial/derivative changes (copy tweaks,
adding a field to an already-styled component) and just build them.

## How to use

1. Copy `_scaffold.html` to `<screen-name>.html` (e.g. `package_modal.html`).
2. Build the screen in the slot, reusing the inlined tokens + component kit. Reuse existing
   app classes; don't invent a new look.
3. Show the states that matter — and don't skip the unglamorous ones (empty / loading /
   error), since that's where most sloppy-UI bugs live. The scaffold's board shows the pattern.
4. Deliver it: open the file in a browser, or publish it as a hosted Artifact. Iterate here,
   then wire the approved version into `app/templates/…`.

Approval is informal — show it, get a 👍.

## Keeping the scaffold honest

`_scaffold.html` inlines `app/static/css/tokens.css` **verbatim** plus a small component kit
built on those tokens — the shared visual foundation, not a mirror of every feature class.
When the app's tokens change, refresh the scaffold's `:root` block so previews don't drift.

Feature CSS (`packages.css`, `submissions.css`) may add its own component classes. When a
screen relies on those, reuse them in the preview rather than re-styling from scratch.
