'use strict';

window.roleDisplayName = function roleDisplayName(name) {
  const labels = {
    'Tenant Administrator': 'Administrator organizacji',
    'Tenant Viewer': 'Obserwator organizacji',
  };
  return labels[String(name || '')] || name;
};
