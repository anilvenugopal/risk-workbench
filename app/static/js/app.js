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

document.addEventListener('alpine:init', () => {
  Alpine.data('packageModal', () => ({
    members: [],
    browseOpen: false,
    nameBlocked: false,
    get canSubmit() {
      return this.members.length > 0 && !this.nameBlocked;
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
      // The removed row may have carried the only blocking error — re-derive
      // after Alpine has flushed the DOM update.
      this.$nextTick(() => this.onSwap());
    },
    onSwap() {
      // Any HTMX swap inside the modal (a row's collision fragment, browse
      // navigation) re-derives the blocked state from what is actually rendered
      // — Save / Save & Sync stay disabled while any row shows the blocking
      // error (issue #17, packageModal parity with importForm).
      this.nameBlocked = !!this.$root.querySelector('.name-collision__error');
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
      });
    },
    recheck(el) {
      // Kind flip: wait a tick so the hidden member_kind :value is flushed
      // before htmx gathers the row's params for the check request.
      this.$nextTick(() => {
        const input = el.closest('.mrow').querySelector('.mrow__name');
        if (input) input.dispatchEvent(new Event('recheck'));
      });
    },
  }));

  // Standalone EDM/RDM import form (issue #17 UX): source file comes first and
  // auto-populates the name (packageModal parity); Import stays disabled until a
  // file is picked, a name is present, and the as-you-type collision check isn't
  // showing the blocking error. Server-side validation still backs all of this —
  // with JS off the button is simply never disabled.
  Alpine.data('importForm', (opts = {}) => ({
    sourceSelected: false,
    appliedSelected: !opts.requireApplied,  // RDM: also needs ≥1 applied EDM
    nameVal: '',
    nameBlocked: false,
    init() {
      this.nameVal = this.$refs.name ? this.$refs.name.value : '';
    },
    get canSubmit() {
      return this.sourceSelected && this.appliedSelected
        && !!this.nameVal.trim() && !this.nameBlocked;
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
          this.nameVal = name.value;
          name.dispatchEvent(new Event('recheck'));  // re-run the collision check
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
      // re-derives the blocked state from what is actually rendered.
      this.nameBlocked = !!this.$root.querySelector('.name-collision__error');
    },
  }));
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

document.addEventListener('htmx:responseError', (e) => {
  const xhr = e.detail.xhr;
  showToast(messageFromResponse(xhr) || `Something went wrong (${xhr.status}).`, 'error');
});
document.addEventListener('htmx:sendError', () => {
  showToast('Network error — please check your connection and try again.', 'error');
});
