'use strict';

(() => {
  const watchers = new Map();

  function stop(key) {
    const watcher = watchers.get(key);
    if (!watcher) return false;
    watcher.active = false;
    if (watcher.timer) window.clearTimeout(watcher.timer);
    watchers.delete(key);
    return true;
  }

  function stopAll() {
    Array.from(watchers.keys()).forEach(stop);
  }

  function watch(key, fetcher, options = {}) {
    if (!key || typeof fetcher !== 'function') throw new Error('Invalid polling watcher');
    stop(key);

    const watcher = {
      active: true,
      timer: null,
      interval: Math.max(250, Number(options.interval || 1500)),
    };
    watchers.set(key, watcher);

    const tick = async () => {
      if (!watcher.active || watchers.get(key) !== watcher) return;
      try {
        const value = await fetcher();
        if (!watcher.active || watchers.get(key) !== watcher) return;
        if (typeof options.onData === 'function') await options.onData(value);
        if (typeof options.stopWhen === 'function' && options.stopWhen(value)) {
          stop(key);
          return;
        }
      } catch (error) {
        if (typeof options.onError === 'function') await options.onError(error);
        if (options.stopOnError) {
          stop(key);
          return;
        }
      }
      if (watcher.active && watchers.get(key) === watcher) {
        watcher.timer = window.setTimeout(tick, watcher.interval);
      }
    };

    if (options.immediate === false) watcher.timer = window.setTimeout(tick, watcher.interval);
    else tick();

    return () => stop(key);
  }

  document.addEventListener('cloudportal:app-hidden', stopAll);

  window.pollingService = Object.freeze({
    watch,
    stop,
    stopAll,
    has: key => watchers.has(key),
  });
})();
