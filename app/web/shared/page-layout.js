'use strict';

(() => {
  function pageHeading(description, actions = []) {
    const copy = node('div', { class: 'page-heading-copy' },
      node('p', { text: description || '' })
    );
    const actionHost = node('div', { class: 'action-group page-heading-actions' }, actions);
    return node('header', { class: 'page-actions page-heading' }, copy, actionHost);
  }

  function filterBar(...controls) {
    return node('div', { class: 'filter-bar', role: 'group', 'aria-label': 'Filtry widoku' }, controls.flat());
  }

  function pageSection(title, description, ...content) {
    return node('section', { class: 'panel page-section' },
      node('header', { class: 'panel-header page-section-header' },
        node('div', {},
          node('h2', { text: title }),
          description ? node('p', { class: 'muted', text: description }) : null
        )
      ),
      ...content
    );
  }

  function tabs(items, activeId, onSelect) {
    return node('div', { class: 'ui-tabs', role: 'tablist' },
      items.map(item => node('button', {
        type: 'button',
        class: 'ui-tab' + (item.id === activeId ? ' active' : ''),
        role: 'tab',
        'aria-selected': String(item.id === activeId),
        onClick: () => onSelect(item.id),
      }, item.label))
    );
  }

  window.uiPageHeading = pageHeading;
  window.uiFilterBar = filterBar;
  window.uiPageSection = pageSection;
  window.uiTabs = tabs;
})();
