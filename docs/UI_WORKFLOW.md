# UI & Implementation Workflow

Fixes two failure modes we hit: screens built from **guesswork**, and many user stories
**landing at once** with no green baseline to diff against. Kept deliberately lightweight —
the preview is a 5-minute sanity check, not a deliverable.

## Two rules

1. **UI-first — for screens with real new layout.** Show a quick rendered HTML preview and
   get a 👍 before wiring it into templates/routes. **Skip it for trivial or derivative
   changes** — copy tweaks, adding a field to an already-styled component, a small variation
   on an existing screen. Just build those.
2. **One vertical slice at a time.** Build a single user story end-to-end, then stop for a
   click before the next. (Bundle small related slices if splitting is silly.)

Scale the effort to the screen. No tables, no written state inventories, no status tracking —
if it feels like paperwork, it's out of scope.

## The preview

This app is server-rendered Jinja2 + HTMX on a real token system, so a static HTML mockup
*is* ~90% of the final template — the markup and classes carry straight over. That's why the
preview pays for itself: iterating a mockup costs minutes; rewiring the wrong template costs a
session.

- Start from [`ui_previews/_scaffold.html`](ui_previews/_scaffold.html) — it inlines the real
  design tokens plus a component kit. Reuse existing classes; don't invent a new look.
- Show the states that matter, and don't skip the unglamorous ones (**empty / loading /
  error**) — that's where most "sloppy UI" bugs live.
- Deliver it so you can open it in a browser: a hosted Artifact link, or the standalone file.
  Iterate in the preview, then wire the approved version.

**Approval is informal** — I show, you thumbs-up in chat.

## A slice is done when

- Preview approved (if it needed one).
- Happy path runs end-to-end on the dev stack — including any worker/poller hop the story
  needs (don't call a queue-backed story "done" without watching a job complete).
- Unit tests pass (`pytest tests/unit`).
- You've clicked the running feature.

Then the next slice starts.

## Advisory: class-convention drift

Two modal conventions exist today — `.modal-*` (`components.css`, the shared kit) vs
`.modal__*` (`packages.css`, feature CSS). When a screen touches these, pick one and stick
with it; unreconciled divergence is a direct source of "it didn't come out like the mockup."
Advisory — not a required artifact.
