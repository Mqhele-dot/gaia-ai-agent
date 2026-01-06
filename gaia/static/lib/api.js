(function () {
  const RETRYABLE_STATUS = new Set([408, 425, 429, 500, 502, 503, 504]);
  const DEFAULT_RETRIES = 2;
  const DEFAULT_DELAY = 400;

  function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function normaliseError(error, status) {
    if (error && typeof error === 'object') {
      return error;
    }
    return { error: error || `Request failed with status ${status}` };
  }

  async function fetchWithRetry(method, path, options = {}) {
    const {
      body,
      headers = {},
      retries = DEFAULT_RETRIES,
      retryDelay = DEFAULT_DELAY,
    } = options;

    const requestId = `req-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const finalHeaders = { Accept: 'application/json', ...headers };
    let payload = body;

    if (body && !(body instanceof FormData) && !finalHeaders['Content-Type']) {
      finalHeaders['Content-Type'] = 'application/json';
      payload = JSON.stringify(body);
    }

    for (let attempt = 0; attempt <= retries; attempt += 1) {
      try {
        const response = await fetch(path, {
          method,
          headers: finalHeaders,
          body: payload,
          cache: 'no-store',
        });

        const contentType = response.headers.get('Content-Type') || '';
        let data;

        if (!response.ok) {
          try {
            data = contentType.includes('application/json') ? await response.json() : await response.text();
          } catch (parseError) {
            data = { error: response.statusText };
          }

          if (
            attempt < retries &&
            (RETRYABLE_STATUS.has(response.status) || response.status >= 500)
          ) {
            await delay(retryDelay * (attempt + 1));
            continue;
          }

          return {
            ok: false,
            status: response.status,
            error: normaliseError(data, response.status),
            requestId,
          };
        }

        if (contentType.includes('application/json')) {
          data = await response.json();
        } else if (contentType.startsWith('text/')) {
          data = await response.text();
        } else {
          data = await response.blob();
        }

        return { ok: true, status: response.status, data, requestId };
      } catch (error) {
        if (attempt < retries) {
          await delay(retryDelay * (attempt + 1));
          continue;
        }
        return {
          ok: false,
          status: 0,
          error: normaliseError(error instanceof Error ? error.message : error, 0),
          requestId,
        };
      }
    }

    return {
      ok: false,
      status: 0,
      error: normaliseError('Unknown failure', 0),
      requestId: `req-${Date.now()}-fail`,
    };
  }

  const client = {
    request(method, path, options) {
      return fetchWithRetry(method, path, options);
    },
    get(path, options) {
      return fetchWithRetry('GET', path, options);
    },
    post(path, body, options = {}) {
      return fetchWithRetry('POST', path, { ...options, body });
    },
    patch(path, body, options = {}) {
      return fetchWithRetry('PATCH', path, { ...options, body });
    },
  };

  window.GaiaApi = client;
})();
