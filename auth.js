'use strict';
const form = document.getElementById('auth-form');
const message = document.getElementById('auth-error');
const submit = document.getElementById('auth-submit');
document.querySelectorAll('[data-toggle]').forEach(button => {
  button.addEventListener('click', () => {
    const input = document.getElementById(button.dataset.toggle);
    const showing = input.type === 'password';
    input.type = showing ? 'text' : 'password';
    button.textContent = showing ? 'Hide' : 'Show';
    button.setAttribute('aria-pressed', String(showing));
    button.setAttribute('aria-label', `${showing ? 'Hide' : 'Show'} ${input.id === 'confirmation' ? 'confirmation password' : 'password'}`);
  });
});
form.addEventListener('submit', async event => {
  event.preventDefault();
  message.hidden = true;
  submit.disabled = true;
  submit.setAttribute('aria-busy', 'true');
  try {
    const data = Object.fromEntries(new FormData(form));
    if (data.confirmation !== undefined && data.password !== data.confirmation) throw new Error('Passwords do not match.');
    const response = await fetch(form.getAttribute('action'), {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-LifeOS-Token': document.querySelector('meta[name="lifeos-token"]').content},
      body: JSON.stringify(data)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not sign in. Please try again.');
    form.reset();
    if ('BroadcastChannel' in window) {
      const channel = new BroadcastChannel('pocket-auth');
      channel.postMessage('changed');
      channel.close();
    }
    window.location.replace((/^\/(?:quick-entry|trips(?:\/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})?)$/.test(result.redirect) ? result.redirect : '/'));
  } catch (error) {
    message.textContent = error instanceof TypeError ? 'Could not connect. Check your connection and try again.' : error.message;
    message.hidden = false;
    message.focus();
  } finally {
    submit.disabled = false;
    submit.removeAttribute('aria-busy');
  }
});
window.addEventListener('pageshow', event => { if (event.persisted) window.location.reload(); });
