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

  function validateNetwork(state, data) {
    const errors = {};
    if (state.ipMode === 'ipam') {
      if (!state.ipamPoolId || !data.pools.some(value => String(value.id) === String(state.ipamPoolId) && value.is_active)) {
        errors.ipamPoolId = 'Wybierz aktywną pulę IPAM.';
      }
    }
    if (state.ipMode === 'static') {
      if (!state.ipv4Address) errors.ipv4Address = 'Podaj IPv4/CIDR.';
      if (!state.ipv4Gateway) errors.ipv4Gateway = 'Podaj gateway.';
    }
    return errors;
  }

  parts.network = { renderNetworkStep, captureNetwork, validateNetwork };
  registerExtension('blueprint-wizard-network', () => {});
})();
