'use strict';

(() => {
function tlsSourceLabel(source) {
  if (source === 'letsencrypt') return 'Let’s Encrypt';
  if (source === 'managed-self-signed') return 'Self-signed zarządzany przez Cloudportal';
  if (source === 'custom') return 'Własny certyfikat';
  return source || 'Nieznane';
}

function tlsDetail(label, value, options = {}) {
  return node('div', { class: 'tls-settings-detail' + (options.wide ? ' wide' : '') },
    node('span', { text: label }),
    node(options.code ? 'code' : 'strong', { text: String(value ?? '—') }));
}

function certificateStatus(certificate) {
  if (!certificate?.present) return badge('Brak certyfikatu', 'danger');
  if (certificate.valid === false) return badge('Nieważny', 'danger');
  if (certificate.matches_hostname === false) return badge('Niedopasowany host', 'warning');
  if (certificate.expires_within_30_days) return badge('Wygasa < 30 dni', 'warning');
  return badge('Poprawny', 'ok');
}

function certificateDescription(certificate) {
  if (!certificate?.present) return 'Brak aktywnego certyfikatu.';
  const names = [...(certificate.dns_names || []), ...(certificate.ip_addresses || [])];
  return names.length ? names.join(', ') : 'Brak SAN w certyfikacie';
}

function fileField(labelText, name, accept, help) {
  const input = node('input', { type: 'file', name, required: true, accept });
  return node('label', { class: 'wide' },
    formFieldLabel(labelText, true),
    input,
    help ? node('span', { class: 'field-help', text: help }) : null);
}

function openCustomTlsForm(status) {
  const body = node('div', { class: 'form-grid' },
    field('Publiczny hostname', 'hostname', {
      required: true,
      value: status.hostname || '',
      placeholder: 'portal.example.com',
      wide: true,
      help: 'Musi być objęty przez SAN/CN w certyfikacie. Zostanie zapisany jako publiczny host instalacji.',
    }),
    fileField(
      'Certyfikat / fullchain PEM',
      'certificate',
      '.pem,.crt,.cer,application/x-pem-file,application/pkix-cert',
      'Dopuszczalny jest pełny chain PEM.'
    ),
    fileField(
      'Klucz prywatny PEM',
      'private_key',
      '.pem,.key,application/x-pem-file',
      'Klucz nie jest zwracany przez API ani wyświetlany ponownie.'
    )
  );

  openModal({
    title: 'Własny certyfikat SSL / TLS',
    eyebrow: 'HTTPS',
    body,
    submitLabel: 'Zastosuj certyfikat',
    wide: true,
    onSubmit: async data => {
      const cert = data.get('certificate');
      const key = data.get('private_key');
      if (!(cert instanceof File) || !cert.size) throw new Error('Wybierz plik certyfikatu PEM.');
      if (!(key instanceof File) || !key.size) throw new Error('Wybierz plik klucza prywatnego PEM.');
      const result = await api('/settings/tls/custom', {
        method: 'POST',
        body: {
          hostname: String(data.get('hostname') || '').trim(),
          certificate_pem: await cert.text(),
          private_key_pem: await key.text(),
        },
      });
      toast('Certyfikat TLS został zastosowany.');
      if (result.hostname && result.hostname !== location.hostname && result.url) {
        location.assign(result.url);
        return false;
      }
      navigate('/admin/settings/tls');
    },
  });
}

function openSelfSignedForm(status) {
  openModal({
    title: 'Wygeneruj certyfikat self-signed',
    eyebrow: 'HTTPS',
    body: node('div', { class: 'form-grid' },
      field('Publiczny hostname / IP', 'hostname', {
        required: true,
        value: status.hostname || '',
        wide: true,
        help: 'Cloudportal wygeneruje nowy certyfikat RSA 3072 ważny przez 365 dni z SAN dla tej nazwy lub IP.',
      })),
    submitLabel: 'Wygeneruj i zastosuj',
    onSubmit: async data => {
      await api('/settings/tls/self-signed', {
        method: 'POST',
        body: { hostname: String(data.get('hostname') || '').trim() },
      });
      toast('Nowy certyfikat self-signed został zastosowany.');
      navigate('/admin/settings/tls');
    },
  });
}

function preferredHostname(candidate, status) {
  if (candidate.certificate?.matches_hostname) return status.hostname || candidate.lineage;
  const names = candidate.certificate?.dns_names || [];
  return names.find(name => !name.startsWith('*.')) || status.hostname || candidate.lineage;
}

function openLetsEncryptForm(candidate, status) {
  const suggested = preferredHostname(candidate, status);
  openModal({
    title: 'Użyj certyfikatu Let’s Encrypt',
    eyebrow: candidate.lineage,
    body: node('div', { class: 'form-grid' },
      field('Publiczny hostname', 'hostname', {
        required: true,
        value: suggested,
        wide: true,
        help: 'Jeśli obecnie wchodzisz po IP, ustaw nazwę DNS objętą certyfikatem. Po zapisaniu używaj tej nazwy w adresie panelu.',
      }),
      node('div', { class: 'tls-settings-modal-summary wide' },
        tlsDetail('Źródło', candidate.path, { code: true, wide: true }),
        tlsDetail('Certyfikat', candidate.certificate_path, { code: true, wide: true }),
        tlsDetail('SAN', certificateDescription(candidate.certificate), { code: true, wide: true }),
        tlsDetail('Ważny do', candidate.certificate?.not_after || '—', { wide: true }))),
    submitLabel: 'Użyj tego certyfikatu',
    wide: true,
    onSubmit: async data => {
      const result = await api('/settings/tls/letsencrypt', {
        method: 'POST',
        body: {
          lineage: candidate.lineage,
          hostname: String(data.get('hostname') || '').trim(),
        },
      });
      toast('Let’s Encrypt został ustawiony dla ' + result.hostname + '.');
      if (result.hostname && result.hostname !== location.hostname && result.url) {
        location.assign(result.url);
        return false;
      }
      navigate('/admin/settings/tls');
    },
  });
}

function letsEncryptCard(candidate, status) {
  const cert = candidate.certificate || {};
  return node('article', { class: 'tls-settings-le-card' },
    node('div', { class: 'tls-settings-le-head' },
      node('div', {},
        node('strong', { text: candidate.lineage }),
        node('small', { text: candidate.path })),
      certificateStatus(cert)),
    node('div', { class: 'tls-settings-detail-grid' },
      tlsDetail('SAN', certificateDescription(cert), { code: true, wide: true }),
      tlsDetail('Ważny od', cert.not_before || '—'),
      tlsDetail('Ważny do', cert.not_after || '—'),
      tlsDetail('Bieżący host', cert.matches_hostname === true ? 'Pasuje' : 'Nie pasuje'),
      tlsDetail('Pliki', 'fullchain.pem + privkey.pem')),
    allowed('settings.update')
      ? node('div', { class: 'tls-settings-actions' },
          button('Użyj certyfikatu', () => openLetsEncryptForm(candidate, status), 'primary'))
      : null
  );
}

async function tlsSettingsView() {
  const [status, discovered] = await Promise.all([
    api('/settings/tls'),
    api('/settings/tls/letsencrypt'),
  ]);
  const certificate = status.certificate || {};
  const candidates = discovered.items || [];

  const current = node('section', { class: 'panel tls-settings-panel' },
    node('div', { class: 'tls-settings-hero' },
      node('div', {},
        node('div', { class: 'tls-settings-title-row' },
          node('h2', { text: 'Aktywny HTTPS' }),
          certificateStatus(certificate)),
        node('p', { class: 'muted', text: 'Certyfikat używany przez panel /ui/ oraz API Cloudportal.' })),
      node('div', { class: 'tls-settings-actions' },
        allowed('settings.update') ? button('Wgraj własny PEM', () => openCustomTlsForm(status)) : null,
        allowed('settings.update') ? button('Nowy self-signed', () => openSelfSignedForm(status)) : null,
        allowed('settings.update') && status.source === 'letsencrypt'
          ? button('Synchronizuj z Let’s Encrypt', async () => {
              await api('/settings/tls/sync', { method: 'POST', body: {} });
              toast('Certyfikat został ponownie zsynchronizowany z Let’s Encrypt.');
              navigate('/admin/settings/tls');
            }, 'primary')
          : null)),
    node('div', { class: 'tls-settings-detail-grid tls-settings-current-grid' },
      tlsDetail('Publiczny URL', status.url, { code: true, wide: true }),
      tlsDetail('Hostname', status.hostname, { code: true }),
      tlsDetail('Port', status.port),
      tlsDetail('Źródło', tlsSourceLabel(status.source)),
      tlsDetail('Automatyczny hook certbot', status.renewal_hook_installed ? 'Aktywny' : 'Nieaktywny'),
      tlsDetail('Subject', certificate.subject || '—', { code: true, wide: true }),
      tlsDetail('Issuer', certificate.issuer || '—', { code: true, wide: true }),
      tlsDetail('SAN', certificateDescription(certificate), { code: true, wide: true }),
      tlsDetail('Ważny od', certificate.not_before || '—'),
      tlsDetail('Ważny do', certificate.not_after || '—'),
      tlsDetail('Dopasowanie hosta', certificate.matches_hostname === true ? 'Tak' : 'Nie')),
    status.source_path
      ? node('div', { class: 'tls-settings-source-note' },
          node('strong', { text: 'Źródło certyfikatu' }),
          node('code', { text: status.source_path }))
      : null
  );

  const letsEncrypt = node('section', { class: 'panel tls-settings-panel' },
    node('div', { class: 'tls-settings-section-head' },
      node('div', {},
        node('h2', { text: 'Let’s Encrypt' }),
        node('p', { class: 'muted', text: 'Automatyczne wykrywanie certyfikatów z ' + discovered.live_dir + '. Cloudportal nie odczytuje prywatnego klucza do przeglądarki.' })),
      badge(String(candidates.length) + ' wykrytych', candidates.length ? 'ok' : 'warning')),
    candidates.length
      ? node('div', { class: 'tls-settings-le-grid' }, candidates.map(item => letsEncryptCard(item, status)))
      : node('div', { class: 'tls-settings-empty' },
          node('strong', { text: 'Nie wykryto certyfikatów Let’s Encrypt' }),
          node('p', { text: 'Oczekiwane są katalogi zawierające fullchain.pem i privkey.pem, np. /etc/letsencrypt/live/kynlab.ddnsfree.com/.' }))
  );

  const certificateNames = certificateDescription(certificate);
  const note = node('section', { class: 'panel tls-settings-warning' },
    node('strong', { text: 'Dopasowanie adresu do certyfikatu' }),
    node('p', {
      text: 'Adres używany w przeglądarce musi być objęty przez SAN certyfikatu. '
        + 'Aktualny endpoint: https://' + status.hostname + ':' + status.port + '/. '
        + 'Nazwy/adresy w aktywnym certyfikacie: ' + certificateNames + '.',
    }));

  dom.content.replaceChildren(
    heading('Konfiguracja certyfikatu HTTPS dla panelu i API.'),
    current,
    letsEncrypt,
    note
  );
}

registerRoutedForm({
  id: 'settings-tls',
  pattern: /^\/admin\/settings\/tls$/,
  parent: 'settings',
  permission: 'settings.read',
  label: 'SSL / TLS',
}, tlsSettingsView);
registerExtension('tls-settings', () => {});
})();
