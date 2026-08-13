// app.js — the small client-side sliver. Alpine handles modal state, the
// Ctrl/Cmd-J shortcut, focus, and arrow-key navigation; HTMX handles the
// search request and result rendering (see the shell's #search-results).
function appShell() {
  return {
    searchOpen: false,
    init() {
      // when the modal opens, focus the input and clear stale results
      this.$watch('searchOpen', (v) => {
        if (v) {
          this.$nextTick(() => {
            const i = document.getElementById('search-input');
            if (i) { i.value = ''; i.focus(); }
            const r = document.getElementById('search-results');
            if (r) r.innerHTML = '<div class="sr-hint">Type to search submissions, workflows, templates, and navigation.</div>';
          });
        }
      });
    },
    onKey(e) {
      // Ctrl/Cmd-J toggles search
      if ((e.ctrlKey || e.metaKey) && (e.key === 'j' || e.key === 'J')) {
        e.preventDefault();
        this.searchOpen = !this.searchOpen;
        return;
      }
      if (!this.searchOpen) return;
      const items = Array.from(document.querySelectorAll('#search-results .sr-item'));
      if (!items.length) return;
      let idx = items.findIndex((el) => el.classList.contains('is-active'));
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        idx = Math.min(items.length - 1, idx + 1);
        items.forEach((el) => el.classList.remove('is-active'));
        items[idx].classList.add('is-active');
        items[idx].scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        idx = Math.max(0, idx - 1);
        items.forEach((el) => el.classList.remove('is-active'));
        items[idx].classList.add('is-active');
        items[idx].scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'Enter') {
        const active = items[idx] || items[0];
        if (active && active.getAttribute('href')) {
          window.location.href = active.getAttribute('href');
        }
      }
    },
  };
}

// ── Alpine components ─────────────────────────────────────────────────────────
// Registered at page load (before Alpine starts) so they exist BEFORE any HTMX
// swap. The package modal is injected via HTMX after load; defining its component
// inline inside that fragment races Alpine's initializer and silently fails, so it
// must live here and be referenced as x-data="packageModal" (no parens).
// Default an EDM/RDM name from a source filename: drop the trailing extension
// (PORTFOLIO.BAK → PORTFOLIO) and cap at the 50-char server limit. The name field
// still enforces the [A-Za-z0-9_-] charset via its pattern; this only sets the guess.
function defaultMemberName(base) {
  const stem = base.replace(/\.[^.]+$/, '');
  return (stem || base).slice(0, 50);
}

// ── Name-collision gating ─────────────────────────────────────────────────────
// The server's collision fragment reports its verdict in data-nc (see
// partials/name_collision.html). Submit is gated on an AFFIRMATIVE pass, not on
// the mere absence of an error: 'ok' (name free) or 'unchecked' (Risk Modeler
// unreachable — the save deliberately fails open) enable it; anything else,
// including "no answer yet", keeps it disabled. That closes the window where a
// freshly picked file left the button live while its check was still in flight.
function ncState(el) {
  return (el && el.dataset.nc) || 'pending';
}
function ncCleared(state) {
  return state === 'ok' || state === 'unchecked';
}
// Typing invalidates the rendered verdict at once — the debounced re-check is
// still ~500ms out, and neither the button nor the message may stand on a stale
// answer for a name that no longer exists.
function ncReset(el) {
  if (!el) return;
  el.removeAttribute('data-nc');
  el.innerHTML = '';
}
// If the check REQUEST fails (server down, session expired) htmx swaps nothing,
// so the verdict would stay pending and the button disabled for good. Fail open
// the same way the server does when Risk Modeler is unreachable — the worker-side
// duplicate-name validation is the backstop either way. Returns false for errors
// that aren't a name check (browse navigation, the modal's own POST).
function ncFailOpen(e) {
  const elt = e.detail && e.detail.elt;
  if (!elt || !elt.classList || !elt.classList.contains('nc-input')) return false;
  const scope = elt.closest('.mrow') || elt.closest('form');
  const el = scope && scope.querySelector('.name-collision');
  if (!el) return false;
  el.dataset.nc = 'unchecked';
  el.innerHTML = '<div class="name-collision__warn" role="status">Couldn’t check '
    + 'this name for duplicates. You can still save, but the import will fail if '
    + 'the name is already taken.</div>';
  return true;
}
// htmx processes the page on DOMContentLoaded, which is *after* Alpine's
// deferred start (see the script order in base/shell.html) — anything that
// dispatches an hx-trigger during x-data init has to wait for it. readyState is
// already "interactive" by then (deferred scripts run after parsing), so it can't
// be the test — track the event, the way htmx tracks it internally.
let htmxReady = document.readyState === 'complete';
document.addEventListener('DOMContentLoaded', () => { htmxReady = true; });
function whenHtmxReady(fn) {
  if (htmxReady) {
    fn();
  } else {
    document.addEventListener('DOMContentLoaded', fn, { once: true });
  }
}

document.addEventListener('alpine:init', () => {
  // Submission form directory field. The "Use this folder" button arrives with an
  // htmx swap, so the click is delegated from the field wrapper.
  Alpine.data('directoryPicker', () => ({
    open: false,
    selected: '',
    init() {
      // $nextTick: the hidden input's x-ref is registered after this init runs.
      this.$nextTick(() => { this.selected = this.$refs.value.value; });
    },
    onPick(e) {
      const btn = e.target.closest('[data-select-dir]');
      if (!btn) return;
      this.selected = btn.dataset.selectDir;
      this.$refs.value.value = this.selected;
      this.open = false;
    },
    clear() {
      this.selected = '';
      this.$refs.value.value = '';
    },
  }));

  Alpine.data('packageModal', () => ({
    members: [],
    browseOpen: true,
    namesCleared: false,
    get canSubmit() {
      return this.members.length > 0 && this.namesCleared;
    },
    onDriveChange(e) {
      const cb = e.target;
      if (cb.type !== 'checkbox' || cb.name !== 'source_paths') return;
      const path = cb.value;
      if (cb.checked) {
        if (!this.members.some((m) => m.path === path)) {
          const base = path.split(/[\\/]/).pop();
          this.members.push({
            path, kind: /rdm/i.test(base) ? 'rdm' : 'edm',
            name: defaultMemberName(base),
          });
        }
      } else {
        this.members = this.members.filter((m) => m.path !== path);
      }
      // A just-added row is unchecked, so the buttons must go dark now rather
      // than when its check lands.
      this.$nextTick(() => this.refreshNames());
    },
    remove(i) {
      // Untick the matching browse checkbox so the picker and member list stay in sync.
      const removed = this.members[i];
      if (removed) {
        const cb = this.$root.querySelector(
          `input[name="source_paths"][value="${CSS.escape(removed.path)}"]`);
        if (cb) cb.checked = false;
      }
      this.members.splice(i, 1);
      // The removed row may have carried the only unresolved check — re-derive
      // after Alpine has flushed the DOM update.
      this.$nextTick(() => this.refreshNames());
    },
    onSwap() {
      // Any HTMX swap inside the modal (a row's collision fragment, browse
      // navigation) re-derives from what is actually rendered.
      this.refreshNames();
    },
    onCheckError(e) {
      if (ncFailOpen(e)) this.refreshNames();
    },
    refreshNames() {
      // Save / Save & Sync light up only once EVERY row's check has come back
      // usable, and each row shows its own "checking…" hint meanwhile so the
      // disabled buttons are never unexplained (issue #17, parity with
      // importForm).
      const rows = Array.from(this.$root.querySelectorAll('.mrow'));
      let cleared = rows.length > 0 && rows.length === this.members.length;
      rows.forEach((row) => {
        const state = ncState(row.querySelector('.name-collision'));
        if (!ncCleared(state)) cleared = false;
        const hint = row.querySelector('.nc-checking');
        const input = row.querySelector('.mrow__name');
        if (hint) hint.hidden = !(input && input.value.trim() && state === 'pending');
      });
      this.namesCleared = cleared;
    },
    markPending(input) {
      if (!input) return;
      ncReset(input.closest('.mrow').querySelector('.name-collision'));
      this.refreshNames();
    },
    initRow(row) {
      // Alpine-cloned x-for rows are invisible to htmx (it only processes
      // server-rendered DOM): wire the row's name-check attributes up, then kick
      // an immediate check for the auto-populated name (issue #17).
      if (!window.htmx) return;
      window.htmx.process(row);
      this.$nextTick(() => {
        const input = row.querySelector('.mrow__name');
        if (input) input.dispatchEvent(new Event('recheck'));
        this.refreshNames();
      });
    },
    recheck(el) {
      // Kind flip: wait a tick so the hidden member_kind :value is flushed
      // before htmx gathers the row's params for the check request. The old
      // verdict was for the other kind, so it is dropped first.
      const input = el.closest('.mrow').querySelector('.mrow__name');
      this.markPending(input);
      this.$nextTick(() => {
        if (input) input.dispatchEvent(new Event('recheck'));
      });
    },
  }));

  // Standalone EDM/RDM import form (issue #17 UX): source file comes first and
  // auto-populates the name (packageModal parity); Import stays disabled until a
  // file is picked, a name is present, and the collision check has come back
  // clear. Server-side validation still backs all of this — with JS off the
  // button is simply never disabled.
  Alpine.data('importForm', (opts = {}) => ({
    sourceSelected: false,
    appliedSelected: !opts.requireApplied,  // RDM: also needs ≥1 applied EDM
    nameVal: '',
    nameState: 'pending',
    init() {
      this.nameVal = this.$refs.name ? this.$refs.name.value : '';
      this.nameState = ncState(this.$root.querySelector('.name-collision'));
      // A 422 re-render arrives with a name but no verdict — kick a check so
      // the form isn't sitting on an unexplained disabled button.
      if (this.nameVal.trim() && this.nameState === 'pending') {
        whenHtmxReady(() => this.$refs.name.dispatchEvent(new Event('recheck')));
      }
    },
    get checking() {
      return !!this.nameVal.trim() && this.nameState === 'pending';
    },
    get canSubmit() {
      return this.sourceSelected && this.appliedSelected
        && !!this.nameVal.trim() && ncCleared(this.nameState);
    },
    onName(e) {
      this.nameVal = e.target.value;
      ncReset(this.$root.querySelector('.name-collision'));
      this.nameState = 'pending';
    },
    onChange(e) {
      const cb = e.target;
      if (cb.type !== 'checkbox') return;
      if (cb.name === 'source_paths') {
        if (cb.checked) {
          // Radio-like: the standalone import takes exactly one source file.
          this.$root.querySelectorAll('input[name="source_paths"]').forEach((o) => {
            if (o !== cb) o.checked = false;
          });
          const base = cb.value.split(/[\\/]/).pop();
          const name = this.$refs.name;
          name.value = defaultMemberName(base);
          this.onName({ target: name });                // the old verdict is void
          name.dispatchEvent(new Event('recheck'));     // re-run the collision check
        }
        this.sourceSelected =
          !!this.$root.querySelector('input[name="source_paths"]:checked');
      } else if (cb.name === 'applied_edm_ids') {
        this.appliedSelected =
          !!this.$root.querySelector('input[name="applied_edm_ids"]:checked');
      }
    },
    onSwap() {
      // Any HTMX swap inside the form (collision fragment, browse navigation)
      // re-derives the verdict from what is actually rendered.
      this.nameState = ncState(this.$root.querySelector('.name-collision'));
    },
    onCheckError(e) {
      if (ncFailOpen(e)) this.onSwap();
    },
  }));

  // Typeahead menu shared by the submission form's CEDANT field and its "links
  // to" picker (CR7/CR8). HTMX fetches and renders the options; this only handles
  // open/close, the keyboard, and committing a pick.
  //
  // Two shapes, told apart by whether the markup provides an x-ref="value":
  //   - cedant     — free text, the chosen name goes straight into the input
  //   - links to   — the id goes into the hidden value input and the chosen
  //                  submission's name is shown as a chip instead
  // With JS off the cedant field degrades to plain text the server still reads;
  // the "links to" picker needs JavaScript.
  //
  // `minTerm` comes from the template, which renders it from the route context's
  // `min_suggest_term` — one number, submission_service.MIN_SUGGEST_TERM, reaching
  // the hx-trigger filter, this component, and the service that answers.
  Alpine.data('typeahead', (opts = {}) => ({
    minTerm: opts.minTerm || 2,
    isOpen: false,
    activeIndex: -1,
    chosen: !!opts.initialLabel,
    chosenLabel: opts.initialLabel || '',
    get options() {
      return Array.from(this.$refs.menu.querySelectorAll('.ta__opt'));
    },
    open() {
      this.isOpen = !!this.$refs.menu.querySelector('.ta__menu');
      if (!this.isOpen) this.activeIndex = -1;
      this.paint();
    },
    close() {
      this.isOpen = false;
      this.activeIndex = -1;
      this.$refs.menu.innerHTML = '';
      this.paint();
    },
    paint() {
      // aria-activedescendant is how a screen reader follows the arrow keys: the
      // focus stays in the input, so the highlighted row has to be named by id.
      this.options.forEach((opt, i) => {
        const active = i === this.activeIndex;
        opt.classList.toggle('is-active', active);
        opt.setAttribute('aria-selected', active ? 'true' : 'false');
      });
      const active = this.options[this.activeIndex];
      if (active) {
        this.$refs.input.setAttribute('aria-activedescendant', active.id);
        active.scrollIntoView({ block: 'nearest' });
      } else {
        this.$refs.input.removeAttribute('aria-activedescendant');
      }
    },
    move(step) {
      const count = this.options.length;
      if (!count) return;
      this.activeIndex = (this.activeIndex + step + count) % count;
      this.paint();
    },
    onInput() {
      // Below the minimum htmx sends nothing, so the menu from a longer term
      // would stay on screen offering matches for text no longer in the input.
      if (this.$refs.input.value.trim().length < this.minTerm) this.close();
    },
    onKey(e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); this.move(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); this.move(-1); }
      else if (e.key === 'Escape') { this.close(); }
      else if (e.key === 'Enter' && this.isOpen && this.activeIndex >= 0) {
        // Only swallow Enter when a menu row is highlighted, so Enter still
        // submits the form when the analyst is just typing.
        e.preventDefault();
        this.pick(this.options[this.activeIndex]);
      }
    },
    pick(opt) {
      if (!opt) return;
      const value = opt.dataset.value;
      const label = opt.dataset.label || value;
      if (this.$refs.value) {
        this.$refs.value.value = value;
        this.chosenLabel = label;
        this.chosen = true;
        this.$refs.input.value = '';
      } else {
        this.$refs.input.value = label;
      }
      this.close();
    },
    clear() {
      if (this.$refs.value) this.$refs.value.value = '';
      this.chosen = false;
      this.chosenLabel = '';
      this.close();
      this.$nextTick(() => this.$refs.input.focus());
    },
  }));

  // Status, Treaty type and Owner filters on the submissions list (D16). The
  // options are already in the DOM; clicking one toggles it and leaves the menu
  // open, and the component writes one hidden input per picked value and
  // dispatches `filter-picked`, which the form listens for.
  //
  // The data-any row clears the rest and reads as selected while nothing else is.
  // The narrowing box (Owner only) hides options in place; it never filters the
  // list itself.
  const MAX_TRIGGER_CHIPS = 3;

  Alpine.data('multiPicker', () => ({
    isOpen: false,
    activeIndex: -1,
    noMatch: false,
    init() {
      // $nextTick: the children's x-ref are registered after this init runs.
      this.$nextTick(() => this.render());
    },
    get allOptions() {
      return Array.from(this.$refs.options.querySelectorAll('.ta__opt'));
    },
    get options() {
      return this.allOptions.filter((opt) => !opt.hidden);
    },
    get anyOption() {
      return this.$refs.options.querySelector('.ta__opt[data-any]');
    },
    get chosen() {
      return this.allOptions.filter(
        (opt) => !opt.hasAttribute('data-any')
          && opt.getAttribute('aria-selected') === 'true');
    },
    toggle() {
      if (this.isOpen) this.close(); else this.open();
    },
    open() {
      this.isOpen = true;
      this.activeIndex = -1;
      if (this.$refs.search) {
        // A term left over from the last visit would hide rows on reopening.
        this.$refs.search.value = '';
        this.narrow();
        this.$nextTick(() => this.$refs.search.focus());
      }
      this.paint();
    },
    close() {
      this.isOpen = false;
      this.activeIndex = -1;
      this.paint();
    },
    narrow() {
      const term = this.$refs.search.value.trim().toLowerCase();
      let matched = 0;
      this.allOptions.forEach((opt) => {
        // The data-any row carries no label and always stays.
        const keep = !opt.dataset.label
          || opt.dataset.label.toLowerCase().includes(term);
        opt.hidden = !keep;
        if (keep && opt.dataset.label) matched += 1;
      });
      this.noMatch = matched === 0;
      this.activeIndex = -1;
      this.paint();
    },
    paint() {
      this.options.forEach((opt, i) => {
        opt.classList.toggle('is-active', i === this.activeIndex);
      });
      const active = this.options[this.activeIndex];
      if (active) {
        this.$refs.trigger.setAttribute('aria-activedescendant', active.id);
        active.scrollIntoView({ block: 'nearest' });
      } else {
        this.$refs.trigger.removeAttribute('aria-activedescendant');
      }
    },
    move(step) {
      const count = this.options.length;
      if (!count) return;
      this.activeIndex = (this.activeIndex + step + count) % count;
      this.paint();
    },
    onKey(e) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (this.isOpen) this.move(1); else this.open();
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (this.isOpen) this.move(-1); else this.open();
      } else if (e.key === 'Escape') {
        this.close();
        this.$refs.trigger.focus();
      } else if (e.key === 'Enter'
                 || (e.key === ' ' && e.target !== this.$refs.search)) {
        // The trigger is a button, so Enter and Space would otherwise fire the
        // click handler and toggle the menu shut over the highlighted row. Space
        // inside the narrowing box is a space — analysts' names have them.
        e.preventDefault();
        if (this.isOpen) this.pick(this.options[this.activeIndex]);
        else this.open();
      }
    },
    onTrigger(e) {
      // A chip's × sits inside the trigger button, so its click arrives here.
      const remove = e.target.closest('.filter-chip__x');
      if (!remove) { this.toggle(); return; }
      const opt = this.allOptions.find(
        (o) => o.dataset.code === remove.dataset.code);
      if (opt) opt.setAttribute('aria-selected', 'false');
      this.apply();
    },
    pick(opt) {
      if (!opt) return;
      if (opt.hasAttribute('data-any')) {
        this.chosen.forEach((o) => o.setAttribute('aria-selected', 'false'));
      } else {
        opt.setAttribute('aria-selected',
          opt.getAttribute('aria-selected') === 'true' ? 'false' : 'true');
      }
      this.apply();
    },
    apply() {
      this.render();
      this.$refs.inputs.dispatchEvent(
        new CustomEvent('filter-picked', { bubbles: true }));
    },
    render() {
      // Ticks, chips and hidden inputs all derive from the options' aria-selected,
      // so init() paints the server-rendered selection without a list request.
      const chosen = this.chosen;
      const any = this.anyOption;
      if (any) any.setAttribute('aria-selected', chosen.length ? 'false' : 'true');
      this.allOptions.forEach((opt) => {
        opt.querySelector('.ta__check').textContent =
          opt.getAttribute('aria-selected') === 'true' ? '✓' : '';
      });
      this.renderInputs(chosen);
      this.renderChips(chosen, any);
    },
    renderInputs(chosen) {
      const name = this.$refs.inputs.dataset.name;
      const empty = this.$refs.inputs.dataset.emptyValue;
      const values = chosen.length
        ? chosen.map((opt) => opt.dataset.code)
        : (empty ? [empty] : []);
      this.$refs.inputs.replaceChildren(...values.map((value) => {
        const input = document.createElement('input');
        input.type = 'hidden';
        input.name = name;
        input.value = value;
        return input;
      }));
    },
    renderChips(chosen, any) {
      if (!chosen.length) {
        const label = document.createElement('span');
        label.className = 'filters__any';
        label.textContent = any ? any.dataset.any : 'Any';
        this.$refs.chips.replaceChildren(label);
        return;
      }
      const shown = chosen.slice(0, MAX_TRIGGER_CHIPS).map((opt) => {
        const chip = document.createElement('span');
        chip.className = 'filter-chip';
        chip.textContent = opt.dataset.label;
        const remove = document.createElement('span');
        remove.className = 'filter-chip__x';
        remove.dataset.code = opt.dataset.code;
        remove.textContent = '×';
        chip.appendChild(remove);
        return chip;
      });
      if (chosen.length > MAX_TRIGGER_CHIPS) {
        const more = document.createElement('span');
        more.className = 'filters__more';
        more.textContent = `+${chosen.length - MAX_TRIGGER_CHIPS}`;
        shown.push(more);
      }
      this.$refs.chips.replaceChildren(...shown);
    },
  }));

  // Treaty year on the submissions list (D16). Typed, not picked — there is no list
  // of years to offer. A year outside the range never becomes a chip, so it never
  // reaches the query: the box shows the message and the committed chips stand.
  Alpine.data('yearChips', (minYear, maxYear) => ({
    error: '',
    get years() {
      return Array.from(this.$refs.chips.querySelectorAll('input')).map(
        (input) => input.value);
    },
    onClick(e) {
      const remove = e.target.closest('.filter-chip__x');
      if (remove) {
        remove.closest('.filter-chip').remove();
        this.apply();
      } else {
        this.$refs.entry.focus();
      }
    },
    onKey(e) {
      if (e.key === 'Enter') {
        // The form has a hidden submit, so Enter would otherwise reload the page
        // around a year the analyst has not committed yet.
        e.preventDefault();
        this.commit();
      } else if (e.key === 'Backspace' && !this.$refs.entry.value) {
        const last = this.$refs.chips.lastElementChild;
        if (!last) return;
        e.preventDefault();
        last.remove();
        this.apply();
      }
    },
    commit() {
      const typed = this.$refs.entry.value.trim();
      if (!typed) return;
      const year = Number(typed);
      if (!/^\d{4}$/.test(typed) || year < minYear || year > maxYear) {
        this.error = `Enter a 4-digit year between ${minYear} and ${maxYear}.`;
        return;
      }
      this.error = '';
      this.$refs.entry.value = '';
      if (this.years.includes(typed)) return;   // already applied — nothing changes
      this.$refs.chips.appendChild(this.chip(typed));
      this.apply();
    },
    chip(year) {
      const chip = document.createElement('span');
      chip.className = 'filter-chip';
      chip.textContent = year;
      const input = document.createElement('input');
      input.type = 'hidden';
      input.name = 'treaty_year';
      input.value = year;
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'filter-chip__x';
      remove.textContent = '×';
      remove.setAttribute('aria-label', `Remove ${year}`);
      chip.append(input, remove);
      return chip;
    },
    apply() {
      this.$refs.chips.dispatchEvent(
        new CustomEvent('filter-picked', { bubbles: true }));
    },
  }));

  // Treaty year follows the inception year until the analyst types their own
  // (CR5, design note 08 D4). Changing the inception date moves the year unless
  // it was edited on this render.
  Alpine.data('treatyYear', () => ({
    edited: false,
    onYearInput() {
      this.edited = !!this.$refs.year.value.trim();
    },
    onDateChange(e) {
      if (this.edited) return;
      const year = (e.target.value || '').slice(0, 4);
      if (/^\d{4}$/.test(year)) this.$refs.year.value = year;
    },
  }));

  // "Sync from Risk Modeler" picker: counts the ticked EDMs for the button label and
  // makes the whole row a hit area. With JS off none of it is needed — the button
  // stays enabled and an empty irp_ids POST is a no-op redirect.
  Alpine.data('syncPicks', () => ({
    count: 0,
    // Back-navigation restores the ticks, so the count comes from the DOM — assuming
    // zero leaves the button disabled over visibly ticked boxes.
    init() { this.onChange(); },
    boxes() {
      return this.$root.querySelectorAll('input[name="irp_ids"]');
    },
    onChange() {
      this.count = this.$root.querySelectorAll(
        'input[name="irp_ids"]:checked').length;
    },
    all(checked) {
      this.boxes().forEach((b) => { b.checked = checked; });
      this.onChange();
    },
    pick(e) {
      // A click that ends a drag-selection keeps the selection, so an exposureId
      // can be copied out of a row without toggling it.
      if (window.getSelection().toString()) return;
      const box = e.currentTarget.querySelector('input[name="irp_ids"]');
      if (!box) return;
      box.checked = !box.checked;
      this.onChange();
    },
  }));
});

// ── Row click → open the submission (D17) ─────────────────────────────────────
// Delegated from the document: htmx replaces the whole #sub-list on every filter,
// sort and page change, and a listener on the table would go with it. A click that
// ends a drag-selection keeps the selection, so a CRM ID can be copied out of a row.
document.addEventListener('click', (e) => {
  const target = e.target instanceof Element ? e.target : null;
  const row = target && target.closest('.data-row[data-href]');
  if (!row || target.closest('a, button, input, label')) return;
  if (window.getSelection().toString()) return;
  window.location.href = row.dataset.href;
});

// ── Local-time stamps ──────────────────────────────────────────────────────────
// The server stores and renders naive-UTC timestamps; <time data-utc="…"> elements
// are rewritten here to the browser's timezone as "YYYY-MM-DD h:mm:ss AM/PM"
// (second granularity — fractional seconds dropped). The raw UTC value stays in
// the title tooltip. Runs at load and again on htmx:load, so stamps swapped in by
// the live #edm-detail poll stay localized.
function localizeUtcTimes(root) {
  const scope = root instanceof Element ? root : document;
  scope.querySelectorAll('time[data-utc]').forEach((el) => {
    const raw = (el.dataset.utc || '').trim();
    // "2026-07-24 18:03:11.482910" → ISO with an explicit Z; values that already
    // carry a zone are parsed as-is.
    const iso = /[Zz]|[+-]\d\d:?\d\d$/.test(raw)
      ? raw.replace(' ', 'T')
      : raw.replace(' ', 'T').replace(/\.\d+$/, '') + 'Z';
    const d = new Date(iso);
    if (isNaN(d)) return;
    const pad = (n) => String(n).padStart(2, '0');
    const date = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    const time = d.toLocaleTimeString(undefined, {
      hour: 'numeric', minute: '2-digit', second: '2-digit', hour12: true,
    });
    el.textContent = `${date} ${time}`;
  });
}
if (document.readyState !== 'loading') {
  localizeUtcTimes(document);
} else {
  document.addEventListener('DOMContentLoaded', () => localizeUtcTimes(document));
}
document.addEventListener('htmx:load', (e) => localizeUtcTimes(e.detail.elt));

// ── Swap state preservation ───────────────────────────────────────────────────
// Two things live only in the DOM, and an HTMX swap replaces DOM: how far the
// analyst has scrolled, and which <details> are open. The EDM detail page's
// #edm-detail wrapper is the scrolling element (.shell is height:100vh with
// overflow hidden — the page itself never scrolls), so a poll that swaps it, or
// that swaps a section inside it, must not cost the analyst their place.
// Recorded for the swap target's subtree before the swap and reapplied after it
// settles: every <details id> keeps its open/closed state, and the scroller
// keeps its offset. Elements the response added (a generated portfolio row) are
// absent from the record and render at their server-rendered default.
//
// Keyed by the target element, not held in one slot: the Portfolios section
// poll swaps the section and out-of-band-swaps two more elements, and an
// analyst opening the breakout modal mid-poll adds a fourth request. With one
// slot, beforeSwap B overwrote A's record and afterSettle A consumed B's — so
// A restored B's open rows and B restored nothing at all.
const swapState = new WeakMap();

function detailsOpenState(root) {
  const state = {};
  if (root.matches && root.matches('details[id]')) state[root.id] = root.open;
  root.querySelectorAll('details[id]').forEach((d) => { state[d.id] = d.open; });
  return state;
}

document.addEventListener('htmx:beforeSwap', (e) => {
  const target = e.detail.target;
  if (!target || !target.querySelectorAll) return;
  // 204 and other no-swap responses (the populated-mid-sync body poll) leave
  // the DOM alone — recording then would strand a stale offset for the next
  // real swap to restore.
  if (e.detail.shouldSwap === false) {
    swapState.delete(target);
    return;
  }
  const scroller = document.getElementById('edm-detail');
  swapState.set(target, {
    details: detailsOpenState(target),
    scrollTop: scroller ? scroller.scrollTop : null,
  });
});

document.addEventListener('htmx:afterSettle', (e) => {
  const target = e.detail && e.detail.target;
  if (!target) return;
  const state = swapState.get(target);
  if (!state) return;
  swapState.delete(target);
  Object.keys(state.details).forEach((id) => {
    const el = document.getElementById(id);
    if (el && el.tagName === 'DETAILS') el.open = state.details[id];
  });
  // The swap may have replaced #edm-detail itself — re-resolve it by id. A
  // recorded 0 is a real offset, so compare against null rather than testing
  // truthiness.
  const scroller = document.getElementById('edm-detail');
  if (scroller && state.scrollTop !== null) scroller.scrollTop = state.scrollTop;
});

// ── Toasts + global error surfacing ───────────────────────────────────────────
// Nothing should fail silently: every HTMX response error / network error raises a
// toast. HTMX drops non-2xx responses by default, so without this an error is
// invisible to the user.
function showToast(message, type) {
  let root = document.getElementById('toast-root');
  if (!root) {
    root = document.createElement('div');
    root.id = 'toast-root';
    root.className = 'toast-root';
    document.body.appendChild(root);
  }
  const el = document.createElement('div');
  el.className = 'toast toast--' + (type || 'error');
  el.setAttribute('role', 'status');
  el.textContent = message;
  el.addEventListener('click', () => el.remove());
  root.appendChild(el);
  setTimeout(() => { el.classList.add('toast--out'); }, 4500);
  setTimeout(() => { el.remove(); }, 5000);
}
window.showToast = showToast;

// Server-pushed toasts: a route can attach `HX-Trigger: {"rwb:toast": {message, type}}`
// (e.g. the fail-open "couldn't check names against Risk Modeler" warning) — htmx
// re-dispatches it as a bubbling DOM event that lands here.
document.addEventListener('rwb:toast', (e) => {
  const d = e.detail || {};
  showToast(d.message || 'Something needs your attention.', d.type || 'warning');
});

// Pull a human message out of an error response — our partials carry the reason in a
// .form-banner--error / .drive-browse__error element; otherwise fall back to status.
function messageFromResponse(xhr) {
  try {
    const ct = (xhr.getResponseHeader('content-type') || '');
    if (ct.indexOf('text/html') !== -1 && xhr.responseText) {
      const doc = new DOMParser().parseFromString(xhr.responseText, 'text/html');
      const banner = doc.querySelector('.form-banner--error, .drive-browse__error, .signin-note');
      const text = banner && banner.textContent.trim();
      if (text) return text;
    }
  } catch (_) { /* fall through to the generic message */ }
  return null;
}

// A breakout refusal names one set of ticked values and one breakout name. Editing
// either makes it wrong, so it clears on edit the way ncReset drops a stale name
// verdict.
function boClearCartError(el) {
  const form = el && el.closest('form');
  const box = form && form.querySelector('#bo-cart-error');
  if (box) box.innerHTML = '';
}

document.addEventListener('htmx:responseError', (e) => {
  const xhr = e.detail.xhr;
  showToast(messageFromResponse(xhr) || `Something went wrong (${xhr.status}).`, 'error');
});
document.addEventListener('htmx:sendError', () => {
  showToast('Network error — please check your connection and try again.', 'error');
});
