'use strict';

(() => {
  async function refreshSession() {
    if (!state.session?.refresh_token) throw new ApiError(401, { detail: 'Sesja wygasła.' });
    if (state.refreshPromise) return state.refreshPromise;

    state.refreshPromise = (async () => {
      const response = await fetch(API + '/auth/refresh', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Request-ID': crypto.randomUUID(),
        },
        body: JSON.stringify({ refresh_token: state.session.refresh_token }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new ApiError(response.status, data);
      saveSession(data);
      state.identity = {
        user: data.user,
        roles: data.roles,
        permissions: data.permissions,
        token_type: 'session',
      };
      return data;
    })().finally(() => {
      state.refreshPromise = null;
    });

    return state.refreshPromise;
  }

  async function request(path, options = {}, canRefresh = true) {
    const method = options.method || 'GET';
    const headers = {
      'X-Request-ID': crypto.randomUUID(),
      'X-Portal-Source': 'Cloudportal-backed',
      ...(options.headers || {}),
    };
    if (options.auth !== false && state.session?.access_token) {
      headers.Authorization = 'Bearer ' + state.session.access_token;
    }
    if (options.body !== undefined) headers['Content-Type'] = 'application/json';
    if (options.idempotent) headers['Idempotency-Key'] = crypto.randomUUID();

    const response = await fetch(API + path, {
      method,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });

    if (response.status === 401 && canRefresh && options.auth !== false && state.session?.refresh_token) {
      try {
        await refreshSession();
      } catch (error) {
        showLogin('Sesja wygasła. Zaloguj się ponownie.');
        throw error;
      }
      return request(path, options, false);
    }

    const data = await response.json().catch(() => ({}));
    if (!response.ok && !(options.allow || []).includes(response.status)) {
      throw new ApiError(response.status, data);
    }
    return data;
  }

  async function text(path, canRefresh = true) {
    const headers = {
      'X-Request-ID': crypto.randomUUID(),
      'X-Portal-Source': 'Cloudportal-backed',
    };
    if (state.session?.access_token) headers.Authorization = 'Bearer ' + state.session.access_token;

    const response = await fetch(API + path, { headers });
    if (response.status === 401 && canRefresh && state.session?.refresh_token) {
      await refreshSession();
      return text(path, false);
    }
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new ApiError(response.status, data);
    }
    return response.text();
  }

  window.cloudportalHttp = Object.freeze({ request, text, refreshSession });
})();
