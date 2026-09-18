'use strict';

(() => {
  try {
    const theme = localStorage.getItem('cloudportal.console.theme');
    if (theme === 'dark' || theme === 'light') document.documentElement.dataset.theme = theme;
  } catch {
    // Light theme from the HTML remains the safe default.
  }
})();
