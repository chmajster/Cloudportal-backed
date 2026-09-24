'use strict';

(() => {
function resourceSummaryCard(iconName, label, count, subtitle, tone, sectionId) {
  return node('button', {
    class: 'my-resources-summary-card my-resources-summary-card-' + tone,
    type: 'button',
    onClick: () => document.getElementById(sectionId)?.scrollIntoView({ behavior: 'smooth', block: 'start' }),
  },
    node('span', { class: 'my-resources-summary-icon', 'aria-hidden': 'true' }, appIcon(iconName)),
    node('span', { class: 'my-resources-summary-copy' },
      node('span', { class: 'my-resources-summary-label', text: label }),
      node('strong', { text: String(count) }),
      node('small', { text: subtitle })),
    node('span', { class: 'my-resources-summary-chevron', 'aria-hidden': 'true' }, appIcon('chevron-right')));
}

function resourceEmptyState(iconName, title, description) {
  return node('div', { class: 'my-resources-empty' },
    node('span', { class: 'my-resources-empty-icon', 'aria-hidden': 'true' }, appIcon(iconName)),
    node('strong', { text: title }),
    node('span', { class: 'muted', text: description }));
}

function resourceSection(id, iconName, title, description, count, content) {
  return node('section', { class: 'panel my-resources-section', id },
    node('div', { class: 'my-resources-section-head' },
      node('div', { class: 'my-resources-section-title' },
        node('span', { class: 'my-resources-section-icon', 'aria-hidden': 'true' }, appIcon(iconName)),
        node('div', {},
          node('h2', { text: title }),
          node('p', { class: 'muted', text: description }))),
      node('span', { class: 'my-resources-section-count', text: String(count) })),
    content);
}

registerExtension('deployments-resource-ui', () => {
  window.DeploymentsResourceUI = Object.freeze({
    resourceSummaryCard,
    resourceEmptyState,
    resourceSection,
  });
});
})();
