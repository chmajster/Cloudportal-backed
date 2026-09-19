'use strict';

(() => {
const CACHE_MS = 30000;
const searchState = { cache: null, loadedAt: 0, activeIndex: -1 };
const searchDom = {
  open: document.querySelector('#global-search-open'),
  dialog: document.querySelector('#global-search-dialog'),
  input: document.querySelector('#global-search-input'),
  close: document.querySelector('#global-search-close'),
  status: document.querySelector('#global-search-status'),
  results: document.querySelector('#global-search-results'),
};

function searchable(value) {
  return String(value ?? '')
    .toLocaleLowerCase('pl-PL')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '');
}

function searchSources() {
  return [
    {
      permission: 'inventory.read', path: '/inventory/vms?limit=200', route: 'inventory', kind: 'VM',
      map: item => ({
        title: item.name || `VM ${item.vm_id}`,
        subtitle: `${item.node || 'węzeł ?'} · VMID ${item.vm_id} · ${statusLabel(item.lifecycle_status)}`,
        keywords: [item.vm_id, item.node, item.management_mode, item.lifecycle_status, item.live?.status],
        entity: { type: 'vm', item },
      }),
    },
    {
      permission: 'deployments.read', path: '/deployments?limit=200', route: 'deployments', kind: 'Wdrożenie',
      map: item => ({
        title: item.name || short(item.id, 18),
        subtitle: `${item.template || 'szablon'} · ${statusLabel(item.status)}`,
        keywords: [item.id, item.provider, item.template, item.executor, item.status],
      }),
    },
    {
      permission: 'jobs.read', path: '/jobs?limit=200', route: 'jobs', kind: 'Zadanie',
      map: item => ({
        title: operationLabel(item.operation),
        subtitle: `${short(item.id, 22)} · ${statusLabel(item.status)}`,
        keywords: [item.id, item.operation, item.status, item.deployment_id],
      }),
    },
    {
      permission: 'providers.read', path: '/providers?limit=200', route: 'providers', kind: 'Platforma',
      map: item => ({
        title: item.name,
        subtitle: CREDENTIAL_TYPE_CONFIG[item.type]?.label || item.type,
        keywords: [item.id, item.type, item.credentials_id],
      }),
    },
    {
      permission: 'blueprints.read', path: '/blueprints?limit=200', route: 'blueprints', kind: 'Blueprint',
      map: item => ({
        title: item.name,
        subtitle: `${item.slug} · v${item.version}`,
        keywords: [item.slug, item.version, ...(item.tags || [])],
      }),
    },
    {
      permission: 'users.read', path: '/users?limit=200', route: 'users', kind: 'Użytkownik',
      map: item => ({
        title: item.username,
        subtitle: item.email || (item.is_service_account ? 'konto serwisowe' : 'konto użytkownika'),
        keywords: [item.email, item.first_name, item.last_name, item.id],
      }),
    },
    {
      permission: 'credentials.read', path: '/credentials?limit=200', route: 'credentials', kind: 'Dane dostępowe',
      map: item => ({
        title: item.name,
        subtitle: CREDENTIAL_TYPE_CONFIG[item.type]?.label || item.type,
        keywords: [item.endpoint, item.username, item.type, item.id],
      }),
    },
    {
      permission: 'hostnames.read', path: '/hostnames?limit=200', route: 'hostnames', kind: 'Nazwa hosta',
      map: item => ({
        title: item.hostname,
        subtitle: statusLabel(item.status),
        keywords: [item.id, item.status, item.resource_id],
      }),
    },
  ];
}

async function loadIndex(force = false) {
  const fresh = searchState.cache && Date.now() - searchState.loadedAt < CACHE_MS;
  if (fresh && !force) return searchState.cache;

  const accessibleRoutes = routes
    .filter(route => allowed(route.permission) && (!state.identity.user.must_change_password || route.id === 'account'));
  const index = accessibleRoutes
    .filter(route => route.navigation !== false)
    .map(route => ({
      kind: 'Widok',
      route: route.id,
      title: route.label,
      subtitle: 'Przejdź do sekcji',
      search: searchable([route.label, route.id].join(' ')),
    }));
  accessibleRoutes
    .filter(route => route.navigation === false && route.navigationParent)
    .forEach(route => {
      const parent = routes.find(candidate => candidate.id === route.navigationParent);
      index.push({
        kind: 'Narzędzie',
        route: route.id,
        title: route.label,
        subtitle: (parent?.label || 'Narzędzia') + ' · narzędzie administracyjne',
        search: searchable([route.label, route.id, parent?.label, 'narzędzie'].join(' ')),
      });
    });

  const sources = searchSources().filter(source => allowed(source.permission));
  await Promise.all(sources.map(async source => {
    try {
      const response = await api(source.path);
      (response.items || []).forEach(item => {
        const mapped = source.map(item);
        index.push({
          kind: source.kind,
          route: source.route,
          title: mapped.title || '—',
          subtitle: mapped.subtitle || '',
          entity: mapped.entity || null,
          search: searchable([
            source.kind, mapped.title, mapped.subtitle, ...(mapped.keywords || []),
          ].join(' ')),
        });
      });
    } catch {
      // One unavailable module must not make global search unusable.
    }
  }));

  searchState.cache = index;
  searchState.loadedAt = Date.now();
  return index;
}

function closeSearch() {
  if (searchDom.dialog?.open) searchDom.dialog.close();
  searchState.activeIndex = -1;
}

async function activateResult(result) {
  closeSearch();
  await navigate(result.route);
  if (
    result.entity?.type === 'vm'
    && hasCommand('inventory.openVm')
    && allowed('vms.read')
  ) {
    await runCommand('inventory.openVm', result.entity.item);
  }
}

function setActive(index) {
  const results = [...searchDom.results.querySelectorAll('.global-search-result')];
  if (!results.length) {
    searchState.activeIndex = -1;
    return;
  }
  searchState.activeIndex = Math.max(0, Math.min(index, results.length - 1));
  results.forEach((item, itemIndex) => {
    const active = itemIndex === searchState.activeIndex;
    item.classList.toggle('active', active);
    item.setAttribute('aria-selected', String(active));
    if (active) item.scrollIntoView({ block: 'nearest' });
  });
}

async function renderSearch(query = '') {
  const value = query.trim();
  if (value.length < 2) {
    searchDom.results.replaceChildren();
    searchDom.status.textContent = 'Wpisz co najmniej 2 znaki.';
    searchState.activeIndex = -1;
    return;
  }

  searchDom.status.textContent = 'Szukam…';
  const index = await loadIndex();
  if (!searchDom.dialog.open || searchDom.input.value.trim() !== value) return;

  const tokens = searchable(value).split(/\s+/).filter(Boolean);
  const results = index
    .filter(item => tokens.every(token => item.search.includes(token)))
    .slice(0, 30);

  searchDom.results.replaceChildren(...results.map((result, indexValue) => node('button', {
    class: 'global-search-result',
    type: 'button',
    role: 'option',
    'aria-selected': 'false',
    onClick: () => activateResult(result),
    onMouseenter: () => setActive(indexValue),
  },
  node('span', { class: 'global-search-kind', text: result.kind }),
  node('span', { class: 'global-search-result-copy' },
    node('strong', { text: result.title }),
    node('small', { text: result.subtitle || '—' })),
  node('span', { class: 'global-search-arrow', 'aria-hidden': 'true', text: '↵' }))));

  searchDom.status.textContent = results.length
    ? `${results.length} wyników · Enter otwiera zaznaczony wynik`
    : 'Brak wyników.';
  setActive(results.length ? 0 : -1);
}

async function openSearch() {
  if (!state.identity || state.identity.user.must_change_password) return;
  if (!searchDom.dialog.open) searchDom.dialog.showModal();
  searchDom.input.value = '';
  searchDom.results.replaceChildren();
  searchDom.status.textContent = 'Ładowanie indeksu…';
  searchState.activeIndex = -1;
  searchDom.input.focus();
  await loadIndex();
  if (searchDom.dialog.open && !searchDom.input.value) {
    searchDom.status.textContent = 'Wpisz co najmniej 2 znaki.';
  }
}

registerExtension('global-search', () => {
  if (Object.values(searchDom).some(value => !value)) {
    throw new Error('Global search shell is incomplete');
  }

  searchDom.open.addEventListener('click', openSearch);
  searchDom.close.addEventListener('click', closeSearch);
  searchDom.dialog.addEventListener('click', event => {
    if (event.target === searchDom.dialog) closeSearch();
  });
  searchDom.input.addEventListener('input', () => { renderSearch(searchDom.input.value); });
  searchDom.input.addEventListener('keydown', event => {
    const count = searchDom.results.querySelectorAll('.global-search-result').length;
    if (event.key === 'ArrowDown' && count) {
      event.preventDefault();
      setActive(searchState.activeIndex + 1);
    } else if (event.key === 'ArrowUp' && count) {
      event.preventDefault();
      setActive(searchState.activeIndex - 1);
    } else if (event.key === 'Enter' && searchState.activeIndex >= 0) {
      event.preventDefault();
      searchDom.results.querySelectorAll('.global-search-result')[searchState.activeIndex]?.click();
    }
  });
  searchDom.dialog.addEventListener('close', () => {
    searchDom.input.value = '';
    searchDom.results.replaceChildren();
  });

  document.addEventListener('cloudportal:app-shown', event => {
    const identity = event.detail?.identity;
    searchDom.open.hidden = Boolean(identity?.user?.must_change_password);
    searchState.cache = null;
    searchState.loadedAt = 0;
  });
  document.addEventListener('cloudportal:app-hidden', () => {
    searchDom.open.hidden = true;
    closeSearch();
  });

  document.addEventListener('keydown', event => {
    const target = event.target;
    const typing = target instanceof HTMLInputElement
      || target instanceof HTMLTextAreaElement
      || target instanceof HTMLSelectElement
      || target?.isContentEditable;

    if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase() === 'k') {
      event.preventDefault();
      if (searchDom.dialog.open) closeSearch();
      else openSearch();
      return;
    }
    if (
      event.key === '/'
      && !typing
      && !event.ctrlKey
      && !event.metaKey
      && !event.altKey
      && state.identity
      && !state.identity.user.must_change_password
    ) {
      event.preventDefault();
      openSearch();
    }
  });
});
})();
