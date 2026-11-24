'use strict';
const themeButton = document.getElementById('theme-toggle');
function setTheme(theme) {
  const light = theme === 'light';
  document.documentElement.dataset.theme = light ? 'light' : 'dark';
  if (themeButton) {
    themeButton.textContent = light ? 'Dark mode' : 'Light mode';
    themeButton.setAttribute('aria-label', light ? 'Use dark theme' : 'Use light theme');
    themeButton.setAttribute('aria-pressed', String(light));
  }
}
try { setTheme(localStorage.getItem('sanjay-theme')); } catch (_) { setTheme('dark'); }
if (themeButton) themeButton.addEventListener('click', () => {
  setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
  try { localStorage.setItem('sanjay-theme', document.documentElement.dataset.theme); } catch (_) { /* Theme still works without browser storage. */ }
});
const workspaceNavigation = document.getElementById('workspace-navigation');
if (workspaceNavigation) {
  const compactNavigation = window.matchMedia('(max-width: 760px)');
  const adaptNavigation = () => { workspaceNavigation.open = !compactNavigation.matches; };
  adaptNavigation();
  compactNavigation.addEventListener('change', adaptNavigation);
}
const importButton = document.getElementById('import');
if (importButton) {
  importButton.addEventListener('click', async () => {
    const file = document.getElementById('bundle').files[0];
    const result = document.getElementById('import-result');
    if (!file) { result.textContent = 'Choose the signed JSON update file first.'; return; }
    if (file.size > 1000000) { result.textContent = 'This file exceeds the 1 MB limit.'; return; }
    importButton.disabled = true;
    result.textContent = 'Verifying signatures and applying eligible updates…';
    try {
      const response = await fetch('/import' + (document.body.dataset.linkedCamps === 'true' ? '?camp=' + document.body.dataset.node : ''), {
        method: 'POST',
        headers: {'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]').content, 'Content-Type': 'application/json'},
        body: await file.arrayBuffer()
      });
      if (!response.ok) throw new Error(await response.text());
      const outcomes = await response.json();
      const applied = outcomes.filter(e => e.status === 'applied').length;
      const pending = outcomes.filter(e => e.status === 'pending').length;
      const rejected = outcomes.filter(e => e.status === 'rejected').length;
      // Counts describe the local event history, not newly imported or acknowledged messages.
      const message = `Update processed. Local history: ${applied} applied, ${pending} pending, ${rejected} requiring review.`;
      window.location.href = '/?view=exchange&message=' + encodeURIComponent(message) + (document.body.dataset.linkedCamps === 'true' ? '&camp=' + document.body.dataset.node : '');
    } catch (error) {
      result.textContent = 'Import not completed: ' + error.message;
      importButton.disabled = false;
    }
  });
}
