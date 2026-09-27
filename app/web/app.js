'use strict';

const loginSubmit = dom.loginForm.querySelector('[data-boot-disabled]');
if (loginSubmit) {
  loginSubmit.disabled = false;
  loginSubmit.textContent = 'Zaloguj';
}

const userMenu = document.querySelector('#user-menu');
const userMenuToggle = document.querySelector('#user-menu-toggle');
const userMenuDropdown = document.querySelector('#user-menu-dropdown');
const currentUserAvatar = document.querySelector('#current-user-avatar');

function setUserMenuOpen(open) {
  const active = Boolean(open);
  userMenuDropdown.hidden = !active;
  userMenuToggle.setAttribute('aria-expanded', String(active));
  userMenu.classList.toggle('open', active);
}
function renderUserMenuIdentity() {
  const user = state.identity?.user || {};
  const parts = [user.first_name, user.last_name].map(value => String(value || '').trim()).filter(Boolean);
  const source = parts.length ? parts : [user.username || 'U'];
  currentUserAvatar.textContent = source.slice(0, 2).map(value => value.charAt(0)).join('').toUpperCase();
  setUserMenuOpen(false);
}
document.addEventListener('cloudportal:app-shown', renderUserMenuIdentity);
document.addEventListener('cloudportal:app-hidden', () => setUserMenuOpen(false));

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
  setUserMenuOpen(false);
  try { await api('/auth/logout', { method: 'POST' }); } catch { /* Local logout still clears the session. */ }
  showLogin('Wylogowano.', 'success');
});
document.querySelectorAll('[data-theme-toggle]').forEach(control => control.addEventListener('click', toggleTheme));
document.querySelector('#user-menu-theme').addEventListener('click', () => {
  toggleTheme();
  setUserMenuOpen(false);
});
userMenuToggle.addEventListener('click', event => {
  event.stopPropagation();
  setUserMenuOpen(userMenuDropdown.hidden);
});
document.querySelector('#user-menu-account').addEventListener('click', () => {
  setUserMenuOpen(false);
  navigate('account');
});
document.addEventListener('click', event => {
  if (!userMenu.contains(event.target)) setUserMenuOpen(false);
});
dom.refreshView.addEventListener('click', async () => {
  dom.refreshView.disabled = true;
  dom.refreshView.classList.add('is-refreshing');
  try { await navigate(state.view); }
  finally {
    dom.refreshView.disabled = false;
    dom.refreshView.classList.remove('is-refreshing');
  }
});
dom.menuToggle.addEventListener('click', toggleSidebar);
dom.sidebarBackdrop.addEventListener('click', () => setMobileMenu(false));
document.querySelector('#modal-close').addEventListener('click', closeModal);
dom.modal.addEventListener('cancel', event => {
  event.preventDefault();
  closeModal();
});
dom.modal.addEventListener('click', event => { if (event.target === dom.modal) closeModal(); });
window.addEventListener('resize', () => {
  setMobileMenu(false);
  updateSidebarToggleState();
});
window.addEventListener('keydown', event => {
  if (event.key === 'Escape' && userMenuToggle.getAttribute('aria-expanded') === 'true') {
    setUserMenuOpen(false);
    userMenuToggle.focus();
    return;
  }
  if (event.key === 'Escape' && typeof window.modalSurfaceOpen === 'function'
      && window.modalSurfaceOpen() && !dom.modal.open) {
    closeModal();
    return;
  }
  if (event.key === 'Escape' && !(typeof window.modalSurfaceOpen === 'function' && window.modalSurfaceOpen())
      && dom.appView.classList.contains('menu-open')) setMobileMenu(false);
});
window.addEventListener('hashchange', () => {
  if (dom.appView.hidden) return;
  const requested = location.hash.slice(1);
  if (requested !== state.routePath) navigate(requested);
});

loadTheme();
loadSidebarState();

(async function boot() {
  loadSession();
  if (!state.session?.access_token) return showLogin();
  try { state.identity = await api('/auth/me'); showApp(); }
  catch { showLogin('Sesja wygasła. Zaloguj się ponownie.'); }
})();
