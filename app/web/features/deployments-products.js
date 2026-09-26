'use strict';

(() => {
async function launchProductBlueprint(item) {
  if (!hasCommand('blueprints.execute')) {
    toast('Uruchamianie Blueprintu nie jest dostępne.', 'error');
    return;
  }
  await navigate('/products/' + encodeURIComponent(item.id)
    + '/' + encodeURIComponent(item.slug || item.name || 'product') + '/create');
}
function productCard(item, avatarById = new Map()) {
  const deployment = item.deployment || {};
  const template = deployment.template || 'VM';
  const provider = deployment.provider || '';
  const avatar = item.avatar_id ? avatarById.get(String(item.avatar_id)) : null;
  const meta = node('div', { class: 'product-card-meta' },
    badge('v' + item.version, 'info'),
    badge(template, ''),
    provider ? badge(provider, '') : null,
    item.requires_approval ? badge('Approval wg polityki globalnej', 'warning') : null);

  return node('article', { class: 'product-card' },
    node('div', { class: 'product-card-top' },
      node('span', { class: 'product-card-icon', 'aria-hidden': 'true' },
        avatar?.data_uri
          ? node('img', { src: avatar.data_uri, alt: '', loading: 'lazy', decoding: 'async' })
          : appIcon('box')),
      node('div', { class: 'product-card-copy' },
        node('strong', { text: item.name }),
        node('small', { class: 'mono muted', text: item.slug }))),
    node('p', {
      class: 'product-card-description',
      text: item.description || 'Gotowy Blueprint do utworzenia maszyny wirtualnej.',
    }),
    meta,
    node('div', { class: 'product-card-actions' },
      button('Utwórz VM', () => launchProductBlueprint(item), 'primary')));
}
const PRODUCT_VIEW_STORAGE_KEY = 'cloudportal.products.view';

function readProductView() {
  try {
    return localStorage.getItem(PRODUCT_VIEW_STORAGE_KEY) === 'list' ? 'list' : 'grid';
  } catch {
    return 'grid';
  }
}

function writeProductView(view) {
  try { localStorage.setItem(PRODUCT_VIEW_STORAGE_KEY, view); } catch { /* Storage may be unavailable. */ }
}

function productList(blueprints, avatarById = new Map()) {
  return node('div', { class: 'product-list' },
    table([
      {
        label: 'Produkt',
        value: item => {
          const avatar = item.avatar_id ? avatarById.get(String(item.avatar_id)) : null;
          return node('div', { class: 'product-list-product' },
            node('span', { class: 'product-list-icon', 'aria-hidden': 'true' },
              avatar?.data_uri
                ? node('img', { src: avatar.data_uri, alt: '', loading: 'lazy', decoding: 'async' })
                : appIcon('box')),
            node('div', { class: 'product-list-copy' },
              node('strong', { text: item.name }),
              node('small', { class: 'mono muted', text: item.slug })));
        },
      },
      {
        label: 'Opis',
        value: item => node('span', {
          class: 'product-list-description',
          text: item.description || 'Gotowy Blueprint do utworzenia maszyny wirtualnej.',
        }),
      },
      { label: 'Wersja', value: item => 'v' + item.version },
      { label: 'Template', value: item => item.deployment?.template || 'VM' },
      { label: 'Provider', value: item => item.deployment?.provider || '—' },
      {
        label: 'Approval',
        value: item => item.requires_approval
          ? badge('Wymagany', 'warning')
          : badge('Nie', ''),
      },
    ], blueprints, item => [
      button('Utwórz VM', () => launchProductBlueprint(item), 'primary'),
    ]));
}

function productsPanel(blueprints, canUseProducts, avatarById = new Map()) {
  let currentView = readProductView();
  const body = node('div', { class: 'product-catalog-body' });
  const gridButton = button('Kafelki', () => setView('grid'));
  const listButton = button('Lista', () => setView('list'));
  const viewToggle = node('div', {
    class: 'product-view-toggle',
    role: 'group',
    'aria-label': 'Sposób wyświetlania produktów',
  }, gridButton, listButton);

  function renderBody() {
    body.replaceChildren();
    if (!canUseProducts) {
      body.append(node('div', {
        class: 'product-catalog-empty',
        text: 'Brak uprawnień do uruchamiania produktów.',
      }));
      return;
    }
    if (!blueprints.length) {
      body.append(node('div', {
        class: 'product-catalog-empty',
        text: 'Brak gotowych Blueprintów. Aktywuj Blueprint widoczny w panelu backendu, aby pojawił się jako produkt.',
      }));
      return;
    }
    if (currentView === 'list') {
      body.append(productList(blueprints, avatarById));
    } else {
      body.append(node('div', { class: 'product-grid' },
        ...blueprints.map(item => productCard(item, avatarById))));
    }
  }

  function refreshToggle() {
    gridButton.classList.toggle('active', currentView === 'grid');
    listButton.classList.toggle('active', currentView === 'list');
    gridButton.setAttribute('aria-pressed', currentView === 'grid' ? 'true' : 'false');
    listButton.setAttribute('aria-pressed', currentView === 'list' ? 'true' : 'false');
  }

  function setView(view) {
    currentView = view === 'list' ? 'list' : 'grid';
    writeProductView(currentView);
    refreshToggle();
    renderBody();
  }

  refreshToggle();
  renderBody();

  return node('section', { class: 'panel product-catalog-panel' },
    node('div', { class: 'product-catalog-header' },
      node('div', {},
        node('span', { class: 'product-catalog-kicker', text: 'Self-service' }),
        node('h2', { text: 'Produkty' }),
        node('p', { class: 'muted', text: 'Wybierz gotowy Blueprint. Formularz pokaże tylko parametry wymagane do utworzenia VM.' })),
      node('div', { class: 'product-catalog-header-actions' },
        badge(String(blueprints.length) + ' dostępnych', blueprints.length ? 'ok' : ''),
        blueprints.length ? viewToggle : null)),
    body);
}

window.DeploymentsProductsUI = Object.freeze({ panel: productsPanel });
registerExtension('deployments-products', () => {});
})();
