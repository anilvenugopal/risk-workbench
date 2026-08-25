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
// Registered at page load (before Alpine starts) so they exist before HTMX swaps.
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

  // Standalone EDM/RDM import form (issue #17 UX): source file comes first and
  // auto-populates the name; Import stays disabled until a
  // file is picked, a name is present, and the collision check has come back
  // clear. Server-side validation still backs all of this — with JS off the
  // button is simply never disabled.
  Alpine.data('importForm', (opts = {}) => ({
    sourceSelected: false,
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
      return this.sourceSelected
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

  // The breakout modal's custom-groups pane (spec 005 FR-018). Pills toggle
  // x-show, so switching dimensions loses no ticked state (T-15), and the chips
  // are derived from the checkboxes, so clearing the boxes clears them. Add
  // breakout is gated the way Import is: disabled until the as-you-type check
  // comes back usable — 'ok', or 'unchecked' when Risk Modeler was unreachable
  // (the group-preview route re-checks at Add either way).
  Alpine.data('breakoutCustom', (opts = {}) => ({
    dim: opts.dim || '',
    nameVal: '',
    nameState: 'pending',
    sel: [],
    get checking() {
      return !!this.nameVal.trim() && this.nameState === 'pending';
    },
    get nameCleared() {
      return !!this.nameVal.trim() && ncCleared(this.nameState);
    },
    resel() {
      this.sel = [...this.$root.querySelectorAll('.bo-checks input:checked')]
        .map((c) => ({
          name: c.name,
          v: c.value,
          shown: c.dataset.display,
          d: c.closest('.bo-checks').dataset.dimlabel,
        }));
    },
    onChange() {
      this.resel();
      boClearCartError(this.$root);
    },
    onName(e) {
      this.nameVal = e.target.value;
      this.nameState = 'pending';
      ncReset(this.$root.querySelector('.name-collision'));
      boClearCartError(this.$root);
    },
    unpick(s) {
      this.$root.querySelectorAll('.bo-checks input:checked').forEach((c) => {
        if (c.name === s.name && c.value === s.v) c.checked = false;
      });
      this.resel();
    },
    onSwap() {
      this.nameState = ncState(this.$root.querySelector('.name-collision'));
    },
    onCheckError(e) {
      if (ncFailOpen(e)) this.onSwap();
    },
    // Keyed on the status: the mount clears isError for a 409 refusal, which
    // also makes htmx report the refusal as successful.
    onAdded(e) {
      if (e.detail.xhr.status !== 200) return;
      this.$root.querySelectorAll('.bo-checks input:checked')
        .forEach((c) => { c.checked = false; });
      this.$root.querySelector('[name=group_label]').value = '';
      boClearCartError(this.$root);
      ncReset(this.$root.querySelector('.name-collision'));
      this.nameVal = '';
      this.nameState = 'pending';
      this.resel();
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

  // Searchable enhancement over a plain <select> (analysis template create/edit —
  // model profile, event rate scheme, output profile). The <select> stays the
  // source of truth (native `required`, and the profile field still drives its
  // hx-get cascade off it) but is hidden once Alpine mounts; a text input filters
  // its live options client-side, matching the "links to" typeahead's
  // degrade-without-JS story but with an already-known, already-rendered option
  // list instead of a server round trip. `sync()` also re-runs after htmx swaps
  // a fresh option list into the cascade target (event rate scheme), since
  // replacing <option> children doesn't fire a native change event.
  Alpine.data('selectSearch', () => ({
    isOpen: false,
    activeIndex: -1,
    query: '',
    init() {
      this.sync();
    },
    get select() {
      return this.$refs.select;
    },
    get allOptions() {
      return Array.from(this.select.options)
        .filter((o) => o.value !== '')
        .map((o) => ({ value: o.value, label: o.textContent.trim() }));
    },
    get filteredOptions() {
      const term = this.query.trim().toLowerCase();
      if (!term) return this.allOptions;
      return this.allOptions.filter((o) => o.label.toLowerCase().includes(term));
    },
    get placeholder() {
      const blank = this.select.querySelector('option[value=""]');
      return blank ? blank.textContent.trim() : 'Search…';
    },
    narrow() {
      this.activeIndex = this.filteredOptions.length === 1 ? 0 : -1;
    },
    sync() {
      const current = this.select.selectedOptions[0];
      this.query = current && current.value ? current.textContent.trim() : '';
      this.isOpen = false;
      this.activeIndex = -1;
    },
    open() {
      // Clears the field to show every option rather than just the one matching
      // the current selection; close() restores the committed label if the
      // analyst leaves without picking a row.
      this.isOpen = true;
      this.query = '';
      this.activeIndex = -1;
    },
    close() {
      this.sync();
    },
    onInput() {
      this.isOpen = true;
      this.narrow();
    },
    move(step) {
      const count = this.filteredOptions.length;
      if (!count) return;
      this.activeIndex = (this.activeIndex + step + count) % count;
      this.$nextTick(() => {
        const active = this.$refs.menu.querySelector('.is-active');
        if (active) active.scrollIntoView({ block: 'nearest' });
      });
    },
    onKey(e) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (this.isOpen) this.move(1); else this.open();
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        this.move(-1);
      } else if (e.key === 'Escape') {
        this.close();
      } else if (e.key === 'Enter' && this.isOpen && this.activeIndex >= 0) {
        // Only swallow Enter when a menu row is highlighted, so Enter still
        // submits the form when the analyst is just typing (typeahead precedent).
        e.preventDefault();
        this.pick(this.$refs.menu.querySelectorAll('.ta__opt')[this.activeIndex]);
      } else if (e.key === 'Tab') {
        this.close();
      }
    },
    pick(opt) {
      if (!opt) return;
      this.select.value = opt.dataset.value;
      this.select.dispatchEvent(new Event('change', { bubbles: true }));
      this.sync();
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

  // Checkbox picker for the "Sync from Risk Modeler" EDM table (name: irp_ids) and
  // the hazard-lookup portfolio table (name: portfolio_ids, observe: true). Counts
  // the ticked boxes for the button label and drives the select-all tri-state.
  // Back-navigation restores the ticks, so the count comes from the DOM — assuming
  // zero leaves the button disabled over visibly ticked boxes. With JS off none of
  // it is needed: the button stays enabled and an empty POST is a no-op redirect.
  //
  // `observe` adds a MutationObserver because the geohaz-cell poll disables and
  // enables a checkbox by OOB-swapping its whole <span> on job completion, and a
  // DOM replacement fires no native change event.
  Alpine.data('checkPicks', ({ name, observe = false } = {}) => ({
    count: 0,
    total: 0,
    observer: null,
    init() {
      this.onChange();
      if (observe) {
        this.observer = new MutationObserver(() => this.onChange());
        this.observer.observe(this.$root, { childList: true, subtree: true });
      }
    },
    destroy() { if (this.observer) this.observer.disconnect(); },
    boxes() {
      return this.$root.querySelectorAll(`input[name="${name}"]:not(:disabled)`);
    },
    onChange() {
      const boxes = this.boxes();
      this.total = boxes.length;
      this.count = Array.from(boxes).filter((box) => box.checked).length;
      const selectAll = this.$refs.selectAll;
      if (!selectAll) return;
      selectAll.checked = this.total > 0 && this.count === this.total;
      selectAll.indeterminate = this.count > 0 && this.count < this.total;
    },
    all(checked) {
      this.boxes().forEach((box) => { box.checked = checked; });
      this.onChange();
    },
    pick(e) {
      // A click that ends a drag-selection keeps the selection, so an exposureId
      // can be copied out of a row without toggling it.
      if (window.getSelection().toString()) return;
      const box = e.currentTarget.querySelector(`input[name="${name}"]`);
      if (!box) return;
      box.checked = !box.checked;
      this.onChange();
    },
  }));

  // Analysis multi-select in the executed-analyses section (spec 010 P-19) —
  // counts ticked rows so the Delete button enables; the boxes themselves are
  // read straight off the DOM by hx-include at click time. init() recounts
  // after each 3s swap (the swap hook below restores the ticks by value).
  Alpine.data('analysisPicks', () => ({
    count: 0,
    init() { this.onChange(); },
    onChange() {
      this.count = this.$root.querySelectorAll(
        'input[name="analysis_ids"]:checked').length;
    },
  }));

  // Execute Suite / Execute Template modal (spec 010). All state lives in the DOM
  // (checkboxes, selects) — this component only reads it, matching syncPicks: no
  // duplicated selection state to drift out of sync with the real form.
  Alpine.data('executeModal', () => ({
    canSubmit: false,
    init() { this.recompute(); },
    onSearch(e) {
      const term = e.target.value.trim().toLowerCase();
      const scope = this.$root.querySelector('#exec-candidates');
      if (!scope) return;
      scope.querySelectorAll('[data-exec-name]').forEach((row) => {
        row.hidden = !!term && !(row.dataset.execName || '').includes(term);
      });
    },
    onChange(e) {
      const target = e.target;
      if (target.name === 'chosen_suite_ids') {
        const details = target.closest('details');
        const fieldset = details && details.querySelector('.exec-row__body');
        if (details) details.open = target.checked;
        if (fieldset) fieldset.disabled = !target.checked;
      }
      const tpl = target.closest('.exec-tpl');
      if (tpl) tpl.classList.toggle('exec-tpl--off', !target.checked);
      const row = target.closest('.exec-row');
      if (row && target.closest('.exec-tpl-list')) {
        const counter = row.querySelector('.exec-row__count-n');
        if (counter) {
          counter.textContent = row.querySelectorAll(
            '.exec-tpl-list input[type="checkbox"]:checked').length;
        }
      }
      this.recompute();
    },
    currencyComplete(scope) {
      const block = scope.querySelector('.exec-currency');
      if (!block) return true;
      return Array.from(block.querySelectorAll('select'))
        .every((select) => select.value !== '');
    },
    recompute() {
      const root = this.$root;
      if (root.dataset.kind === 'suite') {
        let ok = false;
        root.querySelectorAll('.exec-row').forEach((row) => {
          const chosen = row.querySelector('input[name="chosen_suite_ids"]');
          if (!chosen || !chosen.checked) return;
          const hasTemplates = row.querySelectorAll(
            '.exec-tpl-list input[type="checkbox"]:checked').length > 0;
          if (hasTemplates && this.currencyComplete(row)) ok = true;
        });
        this.canSubmit = ok;
      } else {
        const hasTemplates = root.querySelectorAll(
          '.entity-candidate-list input[name="template_ids"]:checked').length > 0;
        this.canSubmit = hasTemplates && this.currencyComplete(root);
      }
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
// Keyed by the target element: up to four swaps are in flight at once during a
// breakout episode, and each afterSettle must read its own record.
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

// Analysis execution submit (spec 010): the execute modal's POST fires this
// alongside rwb:toast (HX-Trigger header) and closes itself; the Analyses
// section refetches on its own (executed_analyses_section.html) — this only
// clears the portfolio picks that were just submitted, so Execute Suite /
// Execute Template disable again (checkPicks reads checked boxes off the
// DOM, so a real 'change' event is what makes it recompute).
document.addEventListener('execution-submitted', () => {
  const checked = document.querySelectorAll('input[name="portfolio_ids"]:checked');
  checked.forEach((box) => { box.checked = false; });
  if (checked.length) checked[0].dispatchEvent(new Event('change', { bubbles: true }));
});

// The freshly-enqueued execute_analysis_batch job writes its first pending
// irp_analysis row worker-side, moments after the request above returns
// (Article 5) — the section's own immediate refetch can land before that
// write happens. Re-fire the event a few times until a row shows up as
// pending/running (the section's hx-trigger then carries "every 3s" and
// keeps itself current from there) or we give up.
document.addEventListener('execution-submitted', () => {
  let attempts = 0;
  const poll = window.setInterval(() => {
    attempts += 1;
    const section = document.getElementById('edm-executed-analyses');
    const live = section && (section.getAttribute('hx-trigger') || '').includes('every 3s');
    if (live || !section || attempts >= 10) { window.clearInterval(poll); return; }
    htmx.trigger(document.body, 'execution-submitted');
  }, 2000);
});

// Swapping the Analyses section (outerHTML, on every poll) rebuilds every row
// from scratch, so an expanded row's <details open> and a ticked delete
// checkbox would otherwise reset — losing the analyst's place mid-inspection
// or mid-selection. Remember both just before the swap and restore them once
// the fresh content lands (a row deleted or no longer deletable simply has no
// box to restore); one bubbling change event makes analysisPicks() recount.
let _analysesReopenIds = null;
let _analysesCheckedIds = null;
document.addEventListener('htmx:beforeSwap', (e) => {
  if (e.detail.target.id !== 'edm-executed-analyses') return;
  _analysesReopenIds = [...e.detail.target.querySelectorAll('.drow[open]')]
    .map((row) => row.id).filter(Boolean);
  _analysesCheckedIds = [...e.detail.target.querySelectorAll(
    'input[name="analysis_ids"]:checked')].map((box) => box.value);
});
document.addEventListener('htmx:afterSwap', () => {
  if (_analysesReopenIds === null && _analysesCheckedIds === null) return;
  const section = document.getElementById('edm-executed-analyses');
  if (section) {
    (_analysesReopenIds || []).forEach((id) => {
      const row = document.getElementById(id);
      if (row) row.open = true;
    });
    let restored = null;
    (_analysesCheckedIds || []).forEach((value) => {
      const box = section.querySelector(
        `input[name="analysis_ids"][value="${value}"]`);
      if (box) { box.checked = true; restored = box; }
    });
    if (restored) restored.dispatchEvent(new Event('change', { bubbles: true }));
  }
  _analysesReopenIds = null;
  _analysesCheckedIds = null;
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
