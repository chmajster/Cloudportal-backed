'use strict';

(() => {
function ldapSettingsForm(config) {
  const fields = node('div', { class: 'form-grid' },
    checkboxField('Włącz logowanie LDAP', 'enabled', Boolean(config.enabled)),
    field('Adres LDAP', 'url', {
      required: true,
      value: config.url || 'ldap://localhost:389',
      placeholder: 'ldaps://ldap.example.com:636',
      wide: true,
      help: 'Obsługiwane: ldap://, ldap:// + StartTLS oraz ldaps://.',
    }),
    checkboxField('StartTLS', 'start_tls', Boolean(config.start_tls)),
    checkboxField('Weryfikuj certyfikat TLS', 'verify_tls', config.verify_tls !== false),
    field('Bind DN', 'bind_dn', {
      value: config.bind_dn || '',
      placeholder: 'cn=cloudportal,ou=services,dc=example,dc=com',
      wide: true,
      help: 'Opcjonalne. Puste pole oznacza próbę anonymous bind.',
    }),
    field('Hasło Bind', 'bind_password', {
      type: 'password',
      value: '',
      autocomplete: 'new-password',
      wide: true,
      placeholder: config.bind_password_configured ? '•••••••• (zapisane)' : 'Hasło konta serwisowego',
      help: config.bind_password_configured
        ? 'Hasło jest już zapisane w formie zaszyfrowanej. Pozostaw puste, aby go nie zmieniać.'
        : 'Sekret zostanie zaszyfrowany kluczem głównym Cloudportal.',
    }),
    field('Base DN', 'base_dn', {
      required: true,
      value: config.base_dn || '',
      placeholder: 'ou=people,dc=example,dc=com',
      wide: true,
    }),
    field('Filtr użytkownika', 'user_filter', {
      required: true,
      value: config.user_filter || '(&(objectClass=person)(uid={username}))',
      placeholder: '(&(objectClass=person)(uid={username}))',
      wide: true,
      help: 'Musi zawierać dokładnie jeden placeholder {username}. Wartość loginu jest escapowana przed wyszukiwaniem.',
    }),
    field('Atrybut loginu', 'username_attribute', { required: true, value: config.username_attribute || 'uid' }),
    field('Atrybut e-mail', 'email_attribute', { required: true, value: config.email_attribute || 'mail' }),
    field('Atrybut imienia', 'first_name_attribute', { required: true, value: config.first_name_attribute || 'givenName' }),
    field('Atrybut nazwiska', 'last_name_attribute', { required: true, value: config.last_name_attribute || 'sn' })
  );

  openModal({
    title: 'Konfiguracja LDAP',
    eyebrow: 'Ustawienia uwierzytelniania',
    body: fields,
    submitLabel: 'Zapisz konfigurację',
    wide: true,
    onSubmit: async (data) => {
      const payload = {
        enabled: data.has('enabled'),
        url: data.get('url'),
        start_tls: data.has('start_tls'),
        verify_tls: data.has('verify_tls'),
        bind_dn: data.get('bind_dn'),
        base_dn: data.get('base_dn'),
        user_filter: data.get('user_filter'),
        username_attribute: data.get('username_attribute'),
        email_attribute: data.get('email_attribute'),
        first_name_attribute: data.get('first_name_attribute'),
        last_name_attribute: data.get('last_name_attribute'),
      };
      if (data.get('bind_password')) payload.bind_password = data.get('bind_password');
      await api('/settings/ldap', { method: 'PUT', body: payload });
      toast('Konfiguracja LDAP zapisana.');
      navigate('settings');
    },
  });
}

async function testLdap() {
  try {
    const result = await api('/settings/ldap/test', { method: 'POST' });
    toast(result.message || 'Połączenie LDAP działa.');
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function settingsView() {
  const config = await api('/settings/ldap');
  const actions = [];
  if (allowed('settings.update')) {
    actions.push(button('Konfiguruj LDAP', () => ldapSettingsForm(config), 'primary'));
    actions.push(button('Testuj LDAP', testLdap));
  }

  dom.content.replaceChildren(
    heading('Ustawienia uwierzytelniania i integracji katalogowych.', actions),
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' },
        node('div', {},
          node('h2', { text: 'LDAP' }),
          node('p', { class: 'muted', text: 'Logowanie katalogowe z automatycznym utworzeniem lokalnego konta i późniejszym przypisaniem ról przez RBAC.' })),
        badge(config.enabled ? 'Włączony' : 'Wyłączony', config.enabled ? 'ok' : 'warning')),
      node('div', { class: 'detail-grid' },
        detailRow('Serwer', config.url || '—'),
        detailRow('Base DN', config.base_dn || '—'),
        detailRow('Bind DN', config.bind_dn || 'Anonymous bind'),
        detailRow('Sekret bind', config.bind_password_configured ? 'Skonfigurowany' : 'Brak'),
        detailRow('TLS', config.url?.startsWith('ldaps://') ? 'LDAPS' : (config.start_tls ? 'StartTLS' : 'Bez TLS')),
        detailRow('Weryfikacja TLS', config.verify_tls ? 'Włączona' : 'Wyłączona'),
        detailRow('Filtr użytkownika', config.user_filter || '—'),
        detailRow('Mapowanie loginu', config.username_attribute || 'uid'),
        detailRow('Mapowanie e-mail', config.email_attribute || 'mail')),
      node('div', { class: 'callout info' },
        node('strong', { text: 'JIT provisioning i RBAC' }),
        node('p', { text: 'Po pierwszym poprawnym logowaniu LDAP Cloudportal tworzy konto z auth_source=ldap bez żadnych ról. Administrator przypisuje role później w Użytkownicy → Role. Hasło pozostaje wyłącznie w LDAP i nie jest zapisywane przez Cloudportal.' }))),
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' }, node('h2', { text: 'Przykładowe filtry' })),
      node('div', { class: 'detail-grid' },
        detailRow('LDAP / LLDAP', '(&(objectClass=person)(uid={username}))'),
        detailRow('Active Directory', '(&(objectClass=user)(sAMAccountName={username}))')))
  );
}

registerView({ id: 'settings', label: 'Ustawienia', icon: 'S', permission: 'settings.read', order: 150 }, settingsView);
})();
