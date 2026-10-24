'use strict';

// Browsers own the installation dialog and toolbar. Use the event only when offered.
const installAction = document.querySelector('[data-install-action]');
const installDialog = document.getElementById('install-dialog');
let pendingInstall = null;

function installedDisplay() {
  return window.matchMedia('(display-mode: standalone)').matches ||
    window.matchMedia('(display-mode: fullscreen)').matches ||
    window.navigator.standalone === true;
}

function updateInstallAction() {
  installAction.hidden = installedDisplay();
  installAction.textContent = pendingInstall ? 'Install Pocket' : 'How to install Pocket';
}

window.addEventListener('beforeinstallprompt', event => {
  event.preventDefault();
  pendingInstall = event;
  updateInstallAction();
});

window.addEventListener('appinstalled', () => {
  pendingInstall = null;
  installAction.hidden = true;
  if (installDialog.open) installDialog.close();
});

installAction.addEventListener('click', async () => {
  if (!pendingInstall) {
    installDialog.showModal();
    return;
  }
  const event = pendingInstall;
  pendingInstall = null;
  updateInstallAction();
  try {
    await event.prompt();
  } catch {
    installDialog.showModal();
  }
});

installDialog.querySelector('[data-install-close]').addEventListener('click', () => installDialog.close());
window.matchMedia('(display-mode: standalone)').addEventListener?.('change', updateInstallAction);
updateInstallAction();
