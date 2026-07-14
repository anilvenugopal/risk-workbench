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
document.addEventListener('alpine:init', () => {
  Alpine.data('packageModal', () => ({
    members: [],
    onDriveChange(e) {
      const cb = e.target;
      if (cb.type !== 'checkbox' || cb.name !== 'source_paths') return;
      const path = cb.value;
      if (cb.checked) {
        if (!this.members.some((m) => m.path === path)) {
          const base = path.split(/[\\/]/).pop();
          this.members.push({ path, kind: /rdm/i.test(base) ? 'rdm' : 'edm', name: base });
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
    },
  }));
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
