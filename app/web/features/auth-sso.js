'use strict';

(() => {
registerExtension('auth-sso', () => {
  const loginButton = document.querySelector('#sso-login');
  const separator = document.querySelector('#sso-separator');

  async function configureLogin() {
    if (!loginButton || !separator) return;
    try {
      const config = await api('/auth/sso/config', { auth: false }, false);
      const enabled = config?.enabled === true;
      loginButton.hidden = !enabled;
      separator.hidden = !enabled;
      if (enabled) loginButton.textContent = 'Zaloguj przez ' + (config.provider_name || 'SSO');
    } catch {
      loginButton.hidden = true;
      separator.hidden = true;
    }
  }

  async function handleCallback() {
    const params = new URLSearchParams(window.location.search);
    const handoff = params.get('sso_handoff');
    const errorCode = params.get('sso_error');
    if (!handoff && !errorCode) return false;

    window.history.replaceState({}, document.title, window.location.pathname + window.location.hash);
    if (handoff) {
      try {
        const pair = await api('/auth/sso/exchange', {
          method: 'POST',
          auth: false,
          body: { token: handoff },
        }, false);
        saveSession(pair);
        state.identity = {
          user: pair.user,
          roles: pair.roles,
          permissions: pair.permissions,
          token_type: 'session',
        };
        showApp();
      } catch (error) {
        await configureLogin();
        showLogin('Logowanie SSO nie powiodło się: ' + error.message);
      }
      return true;
    }

    await configureLogin();
    const messages = {
      provider_denied: 'Logowanie SSO zostało anulowane lub odrzucone przez dostawcę tożsamości.',
      invalid_callback: 'Callback SSO jest nieprawidłowy lub niekompletny.',
      identity_collision: 'Tożsamość SSO koliduje z istniejącym kontem Cloudportal. Administrator musi rozwiązać konflikt kont.',
      authentication_failed: 'Nie udało się zweryfikować odpowiedzi dostawcy SSO.',
    };
    showLogin(messages[errorCode] || 'Logowanie SSO nie powiodło się.');
    return true;
  }

  loginButton?.addEventListener('click', () => {
    window.location.assign('/api/v1/auth/sso/login');
  });

  window.cloudportalSso = Object.freeze({ configureLogin, handleCallback });
});
})();
