'use strict';

dom.loginForm.addEventListener('submit', async event => {
  event.preventDefault();
  const submit = dom.loginForm.querySelector('button[type="submit"]');
  submit.disabled = true;
  setLoginMessage();
  try {
    const data = new FormData(dom.loginForm);
    const pair = await api('/auth/login', { method: 'POST', auth: false, body: { username: data.get('username'), password: data.get('password') } }, false);
    saveSession(pair);
    state.identity = { user: pair.user, roles: pair.roles, permissions: pair.permissions, token_type: 'session' };
    showApp();
  } catch (error) {
    setLoginMessage(error.message);
  } finally { submit.disabled = false; }
});

document.querySelector('#reset-open').addEventListener('click', () => {
  const fields = node('div', { class: 'form-grid' }, field('Token resetu', 'token', { required: true, wide: true }), field('Nowe hasło', 'password', { type: 'password', required: true, minlength: 12 }), field('Powtórz hasło', 'confirm', { type: 'password', required: true, minlength: 12 }));
  openModal({ title: 'Ustaw nowe hasło', eyebrow: 'Reset hasła', body: fields, submitLabel: 'Zapisz hasło', onSubmit: async data => {
    if (data.get('password') !== data.get('confirm')) throw new Error('Hasła nie są identyczne.');
    await api('/auth/reset-password', { method: 'POST', auth: false, body: { token: data.get('token'), password: data.get('password') } }, false);
    setLoginMessage('Hasło zostało zmienione. Możesz się zalogować.', 'success');
  }});
});

document.querySelector('#logout').addEventListener('click', async () => {
  try { await api('/auth/logout', { method: 'POST' }); } catch { /* Local logout still clears the session. */ }
  showLogin('Wylogowano.', 'success');
});
document.querySelectorAll('[data-theme-toggle]').forEach(control => control.addEventListener('click', toggleTheme));
dom.refreshView.addEventListener('click', async () => {
  dom.refreshView.disabled = true;
  dom.refreshView.classList.add('is-refreshing');
  try { await navigate(state.view); }
  finally {
    dom.refreshView.disabled = false;
    dom.refreshView.classList.remove('is-refreshing');
  }
});
dom.menuToggle.addEventListener('click', () => setMobileMenu(!dom.appView.classList.contains('menu-open')));
dom.sidebarBackdrop.addEventListener('click', () => setMobileMenu(false));
document.querySelector('#modal-close').addEventListener('click', closeModal);
dom.modal.addEventListener('cancel', event => {
  event.preventDefault();
  closeModal();
});
dom.modal.addEventListener('click', event => { if (event.target === dom.modal) closeModal(); });
window.addEventListener('resize', () => { if (window.innerWidth > 760) setMobileMenu(false); });
window.addEventListener('keydown', event => {
  if (event.key === 'Escape' && !dom.modal.open && dom.appView.classList.contains('menu-open')) setMobileMenu(false);
});
window.addEventListener('hashchange', () => { if (!dom.appView.hidden && location.hash.slice(1) !== state.view) navigate(location.hash.slice(1)); });

loadTheme();

(async function boot() {
  loadSession();
  if (!state.session?.access_token) return showLogin();
  try { state.identity = await api('/auth/me'); showApp(); }
  catch { showLogin('Sesja wygasła. Zaloguj się ponownie.'); }
})();
