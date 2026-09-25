'use strict';

(() => {
  const parts = window.BlueprintWizardParts = window.BlueprintWizardParts || {};

  function modeCard(state, value, title, description, icon) {
    const active = state.ipMode === value;
    const card = node('button', {
      type: 'button',
      class: 'blueprint-wizard-select-card blueprint-wizard-mode-card' + (active ? ' selected' : ''),
      'aria-pressed': String(active),
    },
      node('span', { class: 'blueprint-wizard-select-card-icon' }, appIcon(icon)),
      node('span', { class: 'blueprint-wizard-select-card-copy' },
        node('strong', { text: title }),
        node('small', { text: description })),
      active ? node('span', { class: 'blueprint-wizard-card-check' }, appIcon('check')) : null
    );
    return card;
  }

  function poolCard(state, pool) {
    const active = String(state.ipamPoolId) === String(pool.id);
    const extra = [];
    if (pool.gateway) extra.push('Gateway ' + pool.gateway);
    if (pool.available_count !== undefined) extra.push('Wolne ' + pool.available_count);
    else if (pool.available !== undefined) extra.push('Wolne ' + pool.available);
    return node('button', {
      type: 'button',
      class: 'blueprint-wizard-select-card' + (active ? ' selected' : ''),
      'aria-pressed': String(active),
    },
      node('span', { class: 'blueprint-wizard-select-card-icon' }, appIcon('globe')),
      node('span', { class: 'blueprint-wizard-select-card-copy' },
        node('strong', { text: pool.name || ('Pula #' + pool.id) }),
        node('code', { text: pool.cidr || 'Brak CIDR' }),
        extra.length ? node('small', { text: extra.join(' · ') }) : null),
      active ? node('span', { class: 'blueprint-wizard-card-check' }, appIcon('check')) : null
    );
  }

  function renderNetworkStep(context) {
    const { state, data, rerender } = context;
    const dhcp = modeCard(state, 'dhcp', 'DHCP', 'Adres zostanie przydzielony przez DHCP.', 'network');
    const ipam = modeCard(state, 'ipam', 'IPAM', 'Cloudportal zarezerwuje kolejny adres z wybranej puli.', 'globe');
    const stat = modeCard(state, 'static', 'Statyczny IPv4', 'Adres, gateway i DNS zostaną zapisane w cloud-init.', 'server');

    [dhcp, ipam, stat].forEach((card, index) => {
      const modes = ['dhcp', 'ipam', 'static'];
      card.addEventListener('click', () => {
        state.ipMode = modes[index];
        rerender();
      });
    });

    const content = node('div', { class: 'blueprint-wizard-step-stack' },
      node('div', { class: 'blueprint-wizard-card-grid blueprint-wizard-card-grid-three' }, dhcp, ipam, stat)
    );

    if (state.ipMode === 'ipam') {
      const pools = data.pools.filter(value => value.is_active);
      const grid = node('div', { class: 'blueprint-wizard-card-grid' });
      if (!pools.length) {
        grid.append(node('div', { class: 'blueprint-wizard-empty' },
          node('strong', { text: 'Brak aktywnych pul IPAM' }),
          node('p', { class: 'muted', text: 'Najpierw dodaj pulę IPAM w module IPAM.' })));
      }
      for (const pool of pools) {
        const card = poolCard(state, pool);
        card.addEventListener('click', () => {
          state.ipamPoolId = String(pool.id);
          rerender();
        });
        grid.append(card);
      }
      content.append(
        node('div', { class: 'blueprint-wizard-section-heading' },
          node('strong', { text: 'Wybierz pulę IPAM' }),
          node('span', { class: 'muted', text: 'Adres zostanie zarezerwowany dopiero podczas wykonania Blueprintu.' })),
        grid
      );
    }

    if (state.ipMode === 'static') {
      content.append(
        node('div', { class: 'form-grid blueprint-wizard-inline-panel' },
          field('IPv4/CIDR', 'ipv4_address', {
            value: state.ipv4Address,
            required: true,
            placeholder: '10.20.30.40/24',
          }),
          field('Gateway', 'ipv4_gateway', {
            value: state.ipv4Gateway,
            required: true,
            placeholder: '10.20.30.1',
          }),
          field('Serwery DNS', 'dns_servers', {
            value: state.dnsServers,
            placeholder: '1.1.1.1, 8.8.8.8',
          }),
          field('Domena DNS', 'dns_domain', {
            value: state.dnsDomain,
            placeholder: 'lab.example.com',
          }))
      );
    } else {
      content.append(
        node('details', { class: 'advanced-options' },
          node('summary', { text: 'Opcjonalne DNS' }),
          node('div', { class: 'advanced-options-body form-grid' },
            field('Serwery DNS', 'dns_servers', {
              value: state.dnsServers,
              placeholder: '1.1.1.1, 8.8.8.8',
            }),
            field('Domena DNS', 'dns_domain', {
              value: state.dnsDomain,
              placeholder: 'lab.example.com',
            })))
      );
    }
    return content;
  }

  function captureNetwork(root, state) {
    const address = root.querySelector('[name="ipv4_address"]');
    const gateway = root.querySelector('[name="ipv4_gateway"]');
    const dns = root.querySelector('[name="dns_servers"]');
    const domain = root.querySelector('[name="dns_domain"]');
    if (address) state.ipv4Address = address.value.trim();
    if (gateway) state.ipv4Gateway = gateway.value.trim();
    if (dns) state.dnsServers = dns.value.trim();
    if (domain) state.dnsDomain = domain.value.trim();
  }

  function ipv4Number(value) {
    const parts = String(value || '').split('.');
    if (parts.length !== 4 || parts.some(part => !/^\d{1,3}$/.test(part))) return null;
    const octets = parts.map(Number);
    if (octets.some(value => value < 0 || value > 255)) return null;
    return (((octets[0] << 24) >>> 0) + (octets[1] << 16) + (octets[2] << 8) + octets[3]) >>> 0;
  }

  function ipv4Interface(value) {
    const match = String(value || '').match(/^([^/]+)\/(\d{1,2})$/);
    if (!match) return null;
    const address = ipv4Number(match[1]);
    const prefix = Number(match[2]);
    if (address === null || prefix < 0 || prefix > 32) return null;
    const mask = prefix === 0 ? 0 : (0xffffffff << (32 - prefix)) >>> 0;
    return { address, prefix, mask };
  }

  function dnsDomainValid(value) {
    if (!value) return true;
    const normalized = String(value).toLowerCase().replace(/\.$/, '');
    if (!normalized || normalized.length > 253) return false;
    return normalized.split('.').every(label =>
      /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label));
  }

  function validateNetwork(state, data) {
    const errors = {};
    if (state.ipMode === 'ipam') {
      if (!state.ipamPoolId || !data.pools.some(value => String(value.id) === String(state.ipamPoolId) && value.is_active)) {
        errors.ipamPoolId = 'Wybierz aktywną pulę IPAM.';
      }
    }
    if (state.ipMode === 'static') {
      const address = ipv4Interface(state.ipv4Address);
      const gateway = ipv4Number(state.ipv4Gateway);
      if (!state.ipv4Address) errors.ipv4Address = 'Podaj IPv4/CIDR.';
      else if (!address) errors.ipv4Address = 'IPv4 musi używać poprawnego CIDR, np. 192.0.2.10/24.';
      if (!state.ipv4Gateway) errors.ipv4Gateway = 'Podaj gateway.';
      else if (gateway === null) errors.ipv4Gateway = 'Gateway musi być poprawnym adresem IPv4.';
      else if (address && (((address.address & address.mask) >>> 0) !== ((gateway & address.mask) >>> 0) || gateway === address.address)) {
        errors.ipv4Gateway = 'Gateway musi należeć do tej samej podsieci i różnić się od adresu VM.';
      }
    }

    const dns = String(state.dnsServers || '').split(/[,\n]+/).map(value => value.trim()).filter(Boolean);
    if (dns.length > 8) errors.dnsServers = 'Możesz podać maksymalnie 8 serwerów DNS.';
    else if (dns.some(value => ipv4Number(value) === null)) errors.dnsServers = 'Serwery DNS muszą być poprawnymi adresami IPv4.';
    if (!dnsDomainValid(state.dnsDomain)) errors.dnsDomain = 'Domena DNS ma niepoprawny format.';
    return errors;
  }

  parts.network = { renderNetworkStep, captureNetwork, validateNetwork };
  registerExtension('blueprint-wizard-network', () => {});
})();
