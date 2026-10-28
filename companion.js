'use strict';

// A tiny native bridge: commands only. Never send records, credentials, or tokens.
if (document.body.dataset.pocketView === 'quick-entry') {
  window.pocketCompanion = Object.freeze({
    version: 1,
    isSaving() { return typeof entrySaving !== 'undefined' && entrySaving; },
    async resume() {
      if (document.getElementById('auth-form')) return true;
      if (typeof refreshAccount !== 'function' || leaving) return false;
      const ready = await refreshAccount();
      if (ready && !document.querySelector('dialog[open]') && !entrySaving) {
        // Do not move the caret while the user is editing a field.
        if (document.activeElement === document.body) document.getElementById('quick-entry').focus();
      }
      return ready;
    },
    async logout() {
      if (document.getElementById('auth-form')) return true;
      return typeof logoutAccount === 'function' && await logoutAccount();
    }
  });
  document.addEventListener('keydown', event => {
    if (event.isComposing || event.repeat) return;
    const dialog = document.querySelector('dialog[open]');
    if (event.key === 'Escape') {
      event.preventDefault();
      if (dialog) dialog.close();
      else window.webkit?.messageHandlers?.pocket?.postMessage('hide');
      return;
    }
    if (event.key !== 'Enter' || !event.metaKey || event.ctrlKey || event.altKey || dialog) return;
    if (!document.getElementById('entry-form')) return;
    event.preventDefault();
    if (entrySaving || !accountReady || leaving) return;
    if (event.shiftKey) document.getElementById('entry-form').requestSubmit();
    else parseQuick();
  });
}
