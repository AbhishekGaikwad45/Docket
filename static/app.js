/* =============================================================================
   Shared browser helpers for the portal.

   Three things that were previously missing or duplicated per page:
     - the sidebar drawer on narrow screens
     - dismissible flash messages
     - toasts and an in-page confirm, replacing blocking window.alert/confirm
   Plus modal open/close with a focus trap, used by every modal in the app.
   ============================================================================= */
(function (window, document) {
  'use strict';

  var FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),' +
    'select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

  /* ---------- Sidebar drawer ---------- */
  function initNavDrawer() {
    var shell = document.getElementById('app-shell');
    var toggle = document.getElementById('nav-toggle');
    if (!shell || !toggle) return;

    var backdrop = shell.querySelector('.sidebar-backdrop');

    function setOpen(open) {
      shell.classList.toggle('nav-open', open);
      toggle.setAttribute('aria-expanded', String(open));
    }

    toggle.addEventListener('click', function () {
      setOpen(!shell.classList.contains('nav-open'));
    });
    if (backdrop) backdrop.addEventListener('click', function () { setOpen(false); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') setOpen(false);
    });
  }

  /* ---------- Flash messages ---------- */
  function initFlashes() {
    document.querySelectorAll('.flash-dismiss').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var note = btn.closest('.flash-note');
        if (note) note.remove();
      });
    });
  }

  /* ---------- Toasts ---------- */
  function toastStack() {
    var stack = document.getElementById('toast-stack');
    if (!stack) {
      stack = document.createElement('div');
      stack.id = 'toast-stack';
      stack.className = 'toast-stack';
      stack.setAttribute('role', 'status');
      stack.setAttribute('aria-live', 'polite');
      document.body.appendChild(stack);
    }
    return stack;
  }

  function toast(message, kind, timeout) {
    var el = document.createElement('div');
    el.className = 'toast toast-' + (kind || 'info');
    el.textContent = message;
    toastStack().appendChild(el);
    window.setTimeout(function () { el.remove(); }, timeout || 5000);
    return el;
  }

  /* ---------- Confirm dialog ---------- */
  /* Promise-based stand-in for window.confirm, so a destructive action reads
     like the rest of the interface and does not freeze the page. */
  function confirmDialog(options) {
    var opts = typeof options === 'string' ? { message: options } : (options || {});

    return new Promise(function (resolve) {
      var overlay = document.createElement('div');
      overlay.className = 'crud-modal open';
      overlay.innerHTML =
        '<div class="crud-modal-box narrow" role="dialog" aria-modal="true" aria-labelledby="confirm-title">' +
          '<div class="crud-modal-header">' +
            '<h2 class="crud-modal-title" id="confirm-title"></h2>' +
            '<button class="crud-modal-close" type="button" data-act="cancel" aria-label="Close">&times;</button>' +
          '</div>' +
          '<div class="crud-modal-body"><p class="delete-confirm-copy" data-role="message"></p></div>' +
          '<div class="crud-modal-footer">' +
            '<button class="btn-secondary-action" type="button" data-act="cancel"></button>' +
            '<button class="btn-primary-action" type="button" data-act="ok"></button>' +
          '</div>' +
        '</div>';

      overlay.querySelector('#confirm-title').textContent = opts.title || 'Confirm';
      overlay.querySelector('[data-role="message"]').textContent = opts.message || '';
      overlay.querySelector('[data-act="ok"]').textContent = opts.confirmText || 'Continue';
      overlay.querySelector('.btn-secondary-action[data-act="cancel"]').textContent =
        opts.cancelText || 'Cancel';

      if (opts.danger) {
        var ok = overlay.querySelector('[data-act="ok"]');
        ok.className = 'btn-action-delete btn-confirm-delete';
      }

      var previousFocus = document.activeElement;

      function close(result) {
        document.removeEventListener('keydown', onKey, true);
        overlay.remove();
        document.body.style.overflow = '';
        if (previousFocus && previousFocus.focus) previousFocus.focus();
        resolve(result);
      }

      function onKey(e) {
        if (e.key === 'Escape') { e.stopPropagation(); close(false); }
        if (e.key === 'Tab') trapTab(e, overlay);
      }

      overlay.addEventListener('click', function (e) {
        if (e.target === overlay) return close(false);
        var act = e.target.closest('[data-act]');
        if (act) close(act.dataset.act === 'ok');
      });

      document.addEventListener('keydown', onKey, true);
      document.body.appendChild(overlay);
      document.body.style.overflow = 'hidden';
      overlay.querySelector('[data-act="ok"]').focus();
    });
  }

  /* ---------- Modal focus handling ---------- */
  function trapTab(e, container) {
    var items = Array.prototype.filter.call(
      container.querySelectorAll(FOCUSABLE),
      function (el) { return el.offsetParent !== null; }
    );
    if (!items.length) return;

    var first = items[0];
    var last = items[items.length - 1];

    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }

  var lastTrigger = null;

  function openModal(modalId, focusSelector) {
    var modal = document.getElementById(modalId);
    if (!modal) return;

    lastTrigger = document.activeElement;
    modal.classList.add('open');
    document.body.style.overflow = 'hidden';

    modal.addEventListener('keydown', modalKeydown);

    var target = focusSelector ? modal.querySelector(focusSelector) : null;
    (target || modal.querySelector(FOCUSABLE) || modal).focus();
  }

  function modalKeydown(e) {
    if (e.key === 'Tab') trapTab(e, e.currentTarget);
  }

  function closeModal(modalId) {
    var modal = document.getElementById(modalId);
    if (!modal || !modal.classList.contains('open')) return;

    modal.classList.remove('open');
    modal.removeEventListener('keydown', modalKeydown);
    document.body.style.overflow = '';

    if (lastTrigger && lastTrigger.focus) lastTrigger.focus();
    lastTrigger = null;
  }

  /* ---------- Misc ---------- */
  function debounce(fn, wait) {
    var timer;
    return function () {
      var args = arguments, self = this;
      window.clearTimeout(timer);
      timer = window.setTimeout(function () { fn.apply(self, args); }, wait || 180);
    };
  }

  window.Portal = {
    toast: toast,
    confirm: confirmDialog,
    openModal: openModal,
    closeModal: closeModal,
    debounce: debounce
  };

  document.addEventListener('DOMContentLoaded', function () {
    initNavDrawer();
    initFlashes();
  });
})(window, document);
