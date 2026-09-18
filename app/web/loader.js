'use strict';

function loadFeatureStyle(path) {
  return new Promise((resolve, reject) => {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = './' + path;
    link.onload = resolve;
    link.onerror = () => reject(new Error('Nie udało się załadować stylu: ' + path));
    document.head.append(link);
  });
}

function loadFeatureScript(path) {
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = './' + path;
    script.async = false;
    script.onload = resolve;
    script.onerror = () => reject(new Error('Nie udało się załadować modułu: ' + path));
    document.head.append(script);
  });
}

(async function loadCloudportalFeatures() {
  const response = await fetch('./manifest.json', { cache: 'no-store' });
  if (!response.ok) throw new Error('Nie udało się pobrać manifestu modułów UI.');
  const manifest = await response.json();

  await Promise.all((manifest.styles || []).map(loadFeatureStyle));
  for (const path of manifest.scripts || []) {
    await loadFeatureScript(path);
  }
  await loadFeatureScript('app.js');
})().catch(error => {
  const target = document.querySelector('#login-error');
  if (target) {
    target.textContent = 'Nie udało się uruchomić panelu: ' + error.message;
    target.hidden = false;
  }
});
