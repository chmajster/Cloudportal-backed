'use strict';

(() => {
  const menu = document.querySelector('#user-menu');
  const toggle = document.querySelector('#user-menu-toggle');
  const dropdown = document.querySelector('#user-menu-dropdown');
  const avatar = document.querySelector('#current-user-avatar');
  const account = document.querySelector('#user-menu-account');
  const theme = document.querySelector('#user-menu-theme');

  function setOpen(open) {
    if (!menu || !toggle || !dropdown) return;
    const active = Boolean(open);
    dropdown.hidden = !active;
    toggle.setAttribute('aria-expanded', String(active));
    menu.classList.toggle('open', active);
  }

  function renderIdentity() {
    if (!avatar || !state.identity?.user) return;
    avatar.textContent = identityInitials(state.identity.user);
    setOpen(false);
  }

  registerExtension('user-menu', () => {
    if (!menu || !toggle || !dropdown) return;

    toggle.addEventListener('click', event => {
      event.stopPropagation();
      setOpen(dropdown.hidden);
    });
    account?.addEventListener('click', () => {
      setOpen(false);
      navigate('account');
    });
    theme?.addEventListener('click', () => {
      toggleTheme();
      setOpen(false);
    });
    document.addEventListener('click', event => {
      if (!menu.contains(event.target)) setOpen(false);
    });
    window.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || toggle.getAttribute('aria-expanded') !== 'true') return;
      setOpen(false);
      toggle.focus();
    });
    document.addEventListener('cloudportal:app-shown', renderIdentity);
    document.addEventListener('cloudportal:app-hidden', () => setOpen(false));
    if (state.identity) renderIdentity();
  });
})();
