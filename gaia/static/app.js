const $ = (selector, scope = document) => scope.querySelector(selector);
const $$ = (selector, scope = document) => Array.from(scope.querySelectorAll(selector));

(function ensureGaiaApi() {
  if (window.GaiaApi) return;

  const RETRYABLE_STATUS = new Set([408, 425, 429, 500, 502, 503, 504]);

  async function fetchWithRetry(method, path, options = {}) {
    const {
      body,
      headers = {},
      retries = 2,
      retryDelay = 300,
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
        const response = await fetch(path, { method, headers: finalHeaders, body: payload, cache: 'no-store' });
        const contentType = response.headers.get('Content-Type') || '';
        let data = null;
        if (contentType.includes('application/json')) {
          try {
            data = await response.json();
          } catch (err) {
            data = await response.text();
          }
        } else {
          data = await response.text();
        }
        if (!response.ok && RETRYABLE_STATUS.has(response.status) && attempt < retries) {
          await new Promise((resolve) => setTimeout(resolve, retryDelay * (attempt + 1)));
          continue;
        }
        return { ok: response.ok, status: response.status, data, requestId, error: response.ok ? null : data };
      } catch (error) {
        if (attempt >= retries) {
          return { ok: false, status: 0, error: error || { error: 'Network failure' }, requestId };
        }
        await new Promise((resolve) => setTimeout(resolve, retryDelay * (attempt + 1)));
      }
    }
    return { ok: false, status: 0, error: { error: 'Request failed' }, requestId };
  }

  window.GaiaApi = {
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
})();

const PRESETS_KEY = 'gaia-dashboard-presets-v1';

const appState = {
  status: null,
  quickChecks: null,
  capsules: [],
  capsuleMeta: new Map(),
  capsulePage: 1,
  capsulePageSize: 25,
  capsuleFilters: { query: '', tag: '', status: '' },
  capsuleSelection: new Set(),
  capsuleVirtual: { rowHeight: 56, scrollTop: 0 },
  researchQueue: [],
  researchHistory: [],
  insights: [],
  simulations: [],
  logs: [],
  activity: [],
  exports: [],
  presets: [],
  autoRefresh: true,
  autoLoops: {},
  learningHistory: [],
  learningRange: 30,
  halted: false,
  activeSection: 'section-actions',
};

const eventBus = new EventTarget();
let statusPollingId = null;

function manageStatusPolling(enabled) {
  if (statusPollingId) {
    clearInterval(statusPollingId);
    statusPollingId = null;
  }
  if (enabled) {
    statusPollingId = setInterval(async () => {
      if (!appState.autoRefresh) return;
      try {
        await refreshStatus();
        await refreshActivity();
      } catch (error) {
        console.warn('Auto refresh failed', error);
      }
    }, 8_000);
  }
}

function applyPreferredTheme() {
  const prefersDark =
    typeof window !== 'undefined' && window.matchMedia
      ? window.matchMedia('(prefers-color-scheme: dark)').matches
      : false;
  if (document.body) {
    document.body.dataset.theme = prefersDark ? 'dark' : 'light';
  }
}

if (typeof window !== 'undefined' && window.matchMedia) {
  const themeWatcher = window.matchMedia('(prefers-color-scheme: dark)');
  if (typeof themeWatcher.addEventListener === 'function') {
    themeWatcher.addEventListener('change', applyPreferredTheme);
  } else if (typeof themeWatcher.addListener === 'function') {
    themeWatcher.addListener(applyPreferredTheme);
  }
}

applyPreferredTheme();

function readPresets() {
  if (typeof window === 'undefined' || !window.localStorage) return [];
  try {
    const raw = window.localStorage.getItem(PRESETS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((preset) => preset && typeof preset === 'object')
      .slice(-10);
  } catch (error) {
    console.warn('Failed to load presets', error);
    return [];
  }
}

function writePresets(presets) {
  if (typeof window === 'undefined' || !window.localStorage) return;
  try {
    window.localStorage.setItem(PRESETS_KEY, JSON.stringify(presets.slice(-10)));
  } catch (error) {
    console.warn('Failed to persist presets', error);
  }
}

function hydratePresets() {
  const presets = readPresets();
  if (presets.length) {
    appState.presets = presets;
  }
}

function createSettingsStore() {
  const defaults = {
    toggles: {
      analyze: false,
      simulate: false,
      learning: false,
      research: false,
      insights: false,
    },
    autoRefresh: true,
  };

  let state = {
    toggles: { ...defaults.toggles },
    autoRefresh: defaults.autoRefresh,
    hydrated: false,
    loading: false,
    error: null,
  };

  const listeners = new Set();

  const snapshot = () => ({
    ...state,
    toggles: { ...state.toggles },
  });

  const notify = () => {
    const current = snapshot();
    listeners.forEach((listener) => listener(current));
  };

  function assign(partial) {
    if (partial.toggles) {
      state.toggles = { ...state.toggles, ...partial.toggles };
    }
    Object.entries(partial).forEach(([key, value]) => {
      if (key !== 'toggles') {
        state[key] = value;
      }
    });
    notify();
  }

  async function hydrate() {
    if (state.loading) return snapshot();
    assign({ loading: true });
    const client = window.GaiaApi;
    if (!client) {
      const error = { error: 'API client unavailable' };
      assign({
        loading: false,
        hydrated: true,
        error,
        toggles: { ...defaults.toggles },
        autoRefresh: defaults.autoRefresh,
      });
      return snapshot();
    }
    const response = await client.get('/settings', { retries: 2, retryDelay: 400 });
    if (response.ok) {
      const data = response.data || {};
      assign({
        toggles: { ...defaults.toggles, ...(data.toggles || {}) },
        autoRefresh:
          typeof data.auto_refresh === 'boolean' ? data.auto_refresh : defaults.autoRefresh,
        loading: false,
        hydrated: true,
        error: null,
      });
    } else {
      assign({
        toggles: { ...defaults.toggles },
        autoRefresh: defaults.autoRefresh,
        loading: false,
        hydrated: true,
        error: response.error,
      });
    }
    return snapshot();
  }

  async function commit() {
    const client = window.GaiaApi;
    if (!client) {
      const error = { error: 'API client unavailable' };
      assign({ error });
      throw error;
    }
    const response = await client.patch(
      '/settings',
      { auto_refresh: state.autoRefresh, toggles: state.toggles },
      { retries: 2, retryDelay: 500 },
    );
    if (!response.ok) {
      assign({ error: response.error });
      throw response.error;
    }
    assign({ error: null });
    return response.data;
  }

  async function setToggle(key, value) {
    const previous = state.toggles[key];
    assign({ toggles: { [key]: value } });
    try {
      await commit();
    } catch (error) {
      assign({ toggles: { [key]: previous } });
      throw error;
    }
    return snapshot();
  }

  async function setAutoRefresh(value) {
    const previous = state.autoRefresh;
    assign({ autoRefresh: value });
    try {
      await commit();
    } catch (error) {
      assign({ autoRefresh: previous });
      throw error;
    }
    return snapshot();
  }

  function subscribe(listener) {
    listeners.add(listener);
    listener(snapshot());
    return () => listeners.delete(listener);
  }

  return {
    hydrate,
    setToggle,
    setAutoRefresh,
    subscribe,
    getState: snapshot,
  };
}

const settingsStore = createSettingsStore();
let settingsErrorNotified = false;

function getApiClient() {
  if (!window.GaiaApi) {
    throw { error: 'API client unavailable' };
  }
  return window.GaiaApi;
}

function ensureSuccess(result) {
  if (!result.ok) {
    const error = result.error || { error: 'Request failed' };
    if (result.requestId && typeof error === 'object') {
      error.requestId = result.requestId;
    }
    throw error;
  }
  return result;
}

const api = {
  async getStatus() {
    const result = await getApiClient().get('/status', { retries: 1, retryDelay: 300 });
    return ensureSuccess(result).data;
  },
  async runQuickChecks() {
    const result = await getApiClient().get('/ops/quick-checks', { retries: 1, retryDelay: 300 });
    return ensureSuccess(result).data;
  },
  async listCapsules(params = {}) {
    const url = new URL('/capsules/list', window.location.origin);
    if (params.tag) url.searchParams.set('tag', params.tag);
    if (params.q) url.searchParams.set('q', params.q);
    const result = await getApiClient().get(url.toString(), { retries: 1, retryDelay: 300 });
    return ensureSuccess(result).data;
  },
  async deleteCapsules(ids, token) {
    const result = await getApiClient().post(
      '/capsules/delete',
      { ids },
      { headers: { 'X-ADMIN-TOKEN': token }, retries: 1, retryDelay: 300 },
    );
    const success = ensureSuccess(result);
    return { data: success.data, requestId: success.requestId };
  },
  async saveCapsule(payload) {
    const result = await getApiClient().post('/capsules/save', payload, { retries: 2, retryDelay: 400 });
    const success = ensureSuccess(result);
    return { data: success.data, requestId: success.requestId };
  },
  async analyzeCapsule(payload) {
    const result = await getApiClient().post('/capsules/analyze', payload, { retries: 2, retryDelay: 400 });
    const success = ensureSuccess(result);
    return { data: success.data, requestId: success.requestId };
  },
  async simulateCapsule(payload) {
    const result = await getApiClient().post('/simulate/run', payload, { retries: 2, retryDelay: 400 });
    const success = ensureSuccess(result);
    return { data: success.data, requestId: success.requestId };
  },
  async runLearning(payload) {
    const result = await getApiClient().post('/learning/step', payload, { retries: 2, retryDelay: 400 });
    const success = ensureSuccess(result);
    return { data: success.data, requestId: success.requestId };
  },
  async proposeUpgrade(payload) {
    const result = await getApiClient().post('/upgrades/propose', payload, { retries: 2, retryDelay: 400 });
    const success = ensureSuccess(result);
    return { data: success.data, requestId: success.requestId };
  },
  async runResearch(payload) {
    const query = typeof payload === 'string' ? payload : payload?.query;
    const hasQuery = query && query.trim().length > 0;
    const path = hasQuery
      ? `/research/explore?q=${encodeURIComponent(query.trim())}`
      : '/research/explore';

    // Prefer GET for compatibility; fallback to POST if no query in path
    const request = hasQuery
      ? getApiClient().get(path, { retries: 2, retryDelay: 400 })
      : getApiClient().post('/research/explore', payload || {}, { retries: 2, retryDelay: 400 });

    const result = await request;
    const success = ensureSuccess(result);
    return { data: success.data, requestId: success.requestId };
  },
  async fetchLearningHistory() {
    const result = await getApiClient().get('/learning/history');
    return ensureSuccess(result).data;
  },
  async fetchInsights() {
    const result = await getApiClient().get('/insights/reflect', { retries: 1, retryDelay: 300 });
    return ensureSuccess(result).data;
  },
  async fetchLogs() {
    const result = await getApiClient().get('/export/logs', { retries: 1, retryDelay: 300 });
    const success = ensureSuccess(result);
    const payload = success.data;
    if (payload instanceof Blob) {
      return await payload.text();
    }
    if (typeof payload === 'string') return payload;
    return JSON.stringify(payload, null, 2);
  },
  async fetchActivity(limit = 50) {
    const result = await getApiClient().get(`/activity/recent?limit=${limit}`, { retries: 1, retryDelay: 300 });
    return ensureSuccess(result).data;
  },
  async exportCapsules(format) {
    const result = await getApiClient().get(`/export/capsules?fmt=${format}`, {
      retries: 1,
      retryDelay: 300,
    });
    const success = ensureSuccess(result);
    const payload = success.data;
    if (payload instanceof Blob) return payload;
    if (typeof payload === 'string') {
      const type = format === 'csv' ? 'text/csv' : format === 'txt' ? 'text/plain' : 'application/json';
      return new Blob([payload], { type });
    }
    return new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
  },
  async killSwitch(token) {
    const result = await getApiClient().post(
      '/admin/kill',
      {},
      { headers: { 'X-ADMIN-TOKEN': token }, retries: 1, retryDelay: 300 },
    );
    const success = ensureSuccess(result);
    return { data: success.data, requestId: success.requestId };
  },
  async autonomyStatus() {
    const result = await getApiClient().get('/autonomy/status', { retries: 1, retryDelay: 300 });
    return ensureSuccess(result).data;
  },
  async autonomyRunOnce() {
    const result = await getApiClient().post('/autonomy/run-once', {}, { retries: 0 });
    return ensureSuccess(result).data;
  },
};

const ui = {
  loading: $('#app-loading'),
  toastTemplate: $('#toast-template'),
  toastRoot: $('#toast-root'),
  navItems: $$('.nav-item'),
  sectionContainer: $('#main-content'),
  quickChecks: {
    pills: $('#quick-check-pills'),
    content: $('#quick-check-content'),
    details: $('#quick-check-details'),
    toggle: $('#quick-checks-expand'),
    rerun: $('#quick-checks-rerun'),
  },
  status: {
    uptime: $('#status-uptime'),
    version: $('#status-version'),
    apiRate: $('#status-api-rate'),
    capsules: $('#status-capsules'),
    avg: $('#status-avg'),
    last: $('#status-last'),
    lastDelta: $('#status-last-delta'),
    trendApi: $('#trend-api'),
    trendCapsules: $('#trend-capsules'),
    trendLatency: $('#trend-latency'),
    trendVersion: $('#trend-version'),
    haltedBanner: $('#halted-banner'),
    environment: $('#status-environment'),
  },
  activity: {
    list: $('#activity-list'),
    count: $('#activity-count'),
  },
  actions: {
    instruction: $('#capsule-instruction'),
    context: $('#capsule-context'),
    tag: $('#capsule-tag'),
    source: $('#capsule-source'),
    instructionCounter: $('#instruction-counter'),
    contextCounter: $('#context-counter'),
    output: $('#actions-output'),
    presetList: $('#preset-list'),
  },
  chips: {
    analyze: $('#chip-auto-analyze'),
    simulate: $('#chip-auto-simulate'),
    learning: $('#chip-auto-learning'),
    research: $('#chip-auto-research'),
    insights: $('#chip-auto-insights'),
  },
  capsuleTable: {
    body: $('#capsule-table'),
    empty: $('#capsule-empty'),
    selectAll: $('#capsule-select-all'),
    pageLabel: $('#capsule-page'),
    prev: $('#capsule-prev'),
    next: $('#capsule-next'),
    bulk: $('#capsule-bulk-actions'),
    bulkCount: $('#capsule-selected-count'),
    bulkDelete: $('#bulk-delete'),
    drawer: $('#capsule-drawer'),
    drawerClose: $('#capsule-drawer-close'),
    drawerContent: $('#capsule-drawer-content'),
    filterQuery: $('#filter-query'),
    filterTag: $('#filter-tag'),
    filterStatus: $('#filter-status'),
    quickFilters: $$('.quick-filters button'),
  },
  research: {
    form: $('#research-form'),
    input: $('#research-query'),
    queue: $('#research-queue'),
    history: $('#research-history'),
  },
  insights: {
    stream: $('#insights-stream'),
  },
  simulation: {
    list: $('#simulation-list'),
  },
  learning: {
    chart: $('#learning-chart'),
    ranges: $$('.learning-range button'),
    events: $('#learning-events'),
    lastDelta: $('#learning-last-delta'),
    version: $('#learning-version'),
  },
  logs: {
    stream: $('#log-stream'),
    level: $('#log-level'),
  },
  exports: {
    history: $('#export-history'),
    buttons: $$('.exports-actions button'),
  },
  commandPalette: {
    root: $('#search-modal'),
    input: $('#command-input'),
    results: $('#command-results'),
    close: $('#command-close'),
  },
  primaryActions: {
    save: $('#action-save'),
    analyze: $('#action-analyze'),
    simulate: $('#action-simulate'),
    learning: $('#action-learning'),
    upgrade: $('#action-upgrade'),
    dryrun: $('#action-dryrun'),
  },
  autoRefresh: $('#auto-refresh'),
  refreshButton: $('#refresh-status'),
  killSwitch: $('#kill-switch'),
  helpButton: $('#open-help'),
};

function toast(message, options = {}) {
  if (!ui.toastTemplate || !ui.toastRoot) return;
  const fragment = ui.toastTemplate.content.cloneNode(true);
  const toastEl = fragment.querySelector('.toast');
  const messageEl = fragment.querySelector('.toast-message');
  const closeBtn = fragment.querySelector('.toast-close');
  const copyBtn = fragment.querySelector('.toast-copy');
  const runId = options.runId || `run-${Date.now()}`;
  messageEl.textContent = message;
  copyBtn.addEventListener('click', () => {
    navigator.clipboard.writeText(runId).catch(() => {});
    copyBtn.textContent = 'Copied';
    setTimeout(() => {
      copyBtn.textContent = 'Copy ID';
    }, 1200);
  });
  closeBtn.addEventListener('click', () => {
    toastEl.remove();
  });
  ui.toastRoot.appendChild(fragment);
  setTimeout(() => toastEl.remove(), options.duration || 6000);
}

settingsStore.subscribe((state) => {
  const { toggles, autoRefresh, hydrated, loading, error } = state;
  document.body.classList.toggle('is-hydrating', !hydrated || loading);
  if (ui.loading) {
    ui.loading.hidden = Boolean(hydrated && !loading);
  }
  Object.entries(ui.chips).forEach(([key, chip]) => {
    if (!chip) return;
    if (key in toggles) {
      chip.checked = Boolean(toggles[key]);
    }
    chip.disabled = !hydrated || loading;
  });
  if (ui.autoRefresh) {
    ui.autoRefresh.checked = Boolean(autoRefresh);
    ui.autoRefresh.disabled = !hydrated || loading;
  }
  appState.autoRefresh = Boolean(autoRefresh);
  manageStatusPolling(appState.autoRefresh);
  processAutoLoops();
  if (hydrated && !loading && error && !settingsErrorNotified) {
    toast(`Settings fallback in use: ${error.error || error.message || error}`, {
      runId: `settings-${Date.now()}`,
    });
    settingsErrorNotified = true;
  }
});

function formatDuration(seconds) {
  const s = Math.max(0, Number(seconds) || 0);
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) {
    const m = Math.floor(s / 60);
    const sec = Math.round(s % 60);
    return `${m}m ${sec}s`;
  }
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${h}h ${m}m`;
}

function humanize(value) {
  if (!value) return '0';
  if (value > 1e6) return `${(value / 1e6).toFixed(1)}M`;
  if (value > 1e3) return `${(value / 1e3).toFixed(1)}k`;
  return `${value}`;
}

function updateStatus(status) {
  appState.status = status;
  if (!status) return;
  ui.status.uptime.textContent = formatDuration(status.uptime_s);
  ui.status.version.textContent = status.version;
  const apiRate = status.rates?.api_per_min ?? 0;
  ui.status.apiRate.textContent = apiRate.toFixed(2);
  ui.status.avg.textContent = `${Number(status.processing_ms_avg || 0).toFixed(1)} ms`;
  ui.status.capsules.textContent = humanize(status.capsules_processed || 0);
  const lastAction = status.last_action_label || status.last_action || 'Idle';
  ui.status.last.textContent = lastAction;
  const lastEvent = status.recent_events?.[0];
  if (lastEvent && lastEvent.ts) {
    const ts = new Date(lastEvent.ts);
    ui.status.lastDelta.textContent = ts.toLocaleTimeString();
  } else {
    ui.status.lastDelta.textContent = '—';
  }
  const deltaApi = status.trends?.api_calls ?? 0;
  const deltaCapsules = status.trends?.capsules ?? 0;
  const deltaLatency = status.trends?.latency_ms ?? 0;
  ui.status.trendApi.textContent = `Δ${deltaApi}`;
  ui.status.trendCapsules.textContent = `Δ${deltaCapsules}`;
  ui.status.trendLatency.textContent = `Δ${deltaLatency.toFixed(1)}`;
  ui.status.trendVersion.textContent = status.learning_version || status.version;
  appState.halted = Boolean(status.halted);
  ui.status.haltedBanner.hidden = !appState.halted;
  updateBadges();
}

function updateBadges() {
  $('#badge-capsules').textContent = appState.capsules.length.toString();
  $('#badge-insights').textContent = appState.insights.length.toString();
  $('#badge-research').textContent = appState.researchHistory.length.toString();
  $('#badge-simulation').textContent = appState.simulations.length.toString();
  $('#badge-learning').textContent = appState.learningHistory.length.toString();
  $('#badge-logs').textContent = appState.logs.length.toString();
  $('#badge-exports').textContent = appState.exports.length.toString();
}

function renderQuickChecks(result) {
  appState.quickChecks = result;
  if (!result) return;
  ui.quickChecks.pills.innerHTML = '';
  const { summary, checks } = result;
  const total = summary.passed + summary.warned + summary.failed;
  const summaryItems = [
    { type: 'pass', label: `${summary.passed} Pass` },
    { type: 'warn', label: `${summary.warned} Warn` },
    { type: 'fail', label: `${summary.failed} Fail` },
  ];
  summaryItems.forEach((item) => {
    const pill = document.createElement('span');
    pill.className = `check-pill ${item.type}`;
    pill.textContent = item.label;
    ui.quickChecks.pills.appendChild(pill);
  });
  ui.quickChecks.content.innerHTML = '';
  checks.forEach((check) => {
    const card = document.createElement('div');
    card.className = `quick-check-card ${check.status}`;
    const heading = document.createElement('h3');
    heading.textContent = check.label;
    const status = document.createElement('span');
    status.className = 'status';
    status.textContent = check.status.toUpperCase();
    const detail = document.createElement('p');
    detail.textContent = check.detail;
    const remediation = document.createElement('p');
    remediation.innerHTML = `<strong>Remediation:</strong> ${check.remediation}`;
    card.append(heading, status, detail, remediation);
    ui.quickChecks.content.appendChild(card);
  });
  toast(`Quick checks complete (${total} checks)`, { runId: `qc-${Date.now()}` });
}

function updateCounters() {
  const instructionLength = appState.actionsInstruction?.length || 0;
  const contextLength = appState.actionsContext?.length || 0;
  ui.actions.instructionCounter.textContent = `${instructionLength} chars • ${Math.ceil(instructionLength / 4)} tokens`;
  ui.actions.contextCounter.textContent = `${contextLength} chars • ${Math.ceil(contextLength / 4)} tokens`;
  renderActionKPIs();
}

function setInstruction(value) {
  appState.actionsInstruction = value;
  updateCounters();
}

function setContext(value) {
  appState.actionsContext = value;
  updateCounters();
}

function renderActionKPIs() {
  const tokens = Math.ceil((appState.actionsInstruction?.length || 0 + appState.actionsContext?.length || 0) / 4);
  const auto = ['analyze', 'simulate', 'learning', 'research', 'insights']
    .filter((key) => ui.chips[key]?.checked)
    .map((key) => key.charAt(0).toUpperCase() + key.slice(1));
  renderKPIs('#actions-kpis', [
    { label: 'Tokens', value: tokens.toString() },
    { label: 'Presets', value: appState.presets.length.toString() },
    { label: 'Auto', value: auto.join(', ') || 'Manual' },
  ]);
}

function buildCapsuleText() {
  const instruction = appState.actionsInstruction || '';
  const context = appState.actionsContext || '';
  if (!context.trim()) return instruction.trim();
  return `${instruction.trim()}\n\nContext:\n${context.trim()}`;
}

function renderKPIs(containerId, entries) {
  const container = $(containerId);
  if (!container) return;
  container.innerHTML = '';
  entries.forEach((entry) => {
    const card = document.createElement('div');
    card.className = 'kpi-card';
    const label = document.createElement('span');
    label.className = 'label';
    label.textContent = entry.label;
    const value = document.createElement('strong');
    value.textContent = entry.value;
    const hint = document.createElement('span');
    hint.className = 'hint';
    hint.textContent = entry.hint || '';
    card.append(label, value);
    if (entry.hint) card.appendChild(hint);
    container.appendChild(card);
  });
}

function capsuleMatchesFilters(capsule) {
  const { query, tag, status } = appState.capsuleFilters;
  if (tag && capsule.tag !== tag) return false;
  if (query && !capsule.text.toLowerCase().includes(query.toLowerCase())) return false;
  if (status) {
    const meta = appState.capsuleMeta.get(capsule.id);
    if (!meta || meta.status !== status) return false;
  }
  return true;
}

function filteredCapsules() {
  return appState.capsules.filter(capsuleMatchesFilters);
}

function renderCapsuleTable() {
  const rows = filteredCapsules();
  appState.capsuleSelection.forEach((id) => {
    if (!rows.find((capsule) => capsule.id === id)) {
      appState.capsuleSelection.delete(id);
    }
  });
  const startIndex = (appState.capsulePage - 1) * appState.capsulePageSize;
  const pageItems = rows.slice(startIndex, startIndex + appState.capsulePageSize);
  if (!appState.capsuleVirtual.viewport) {
    ui.capsuleTable.body.innerHTML = '';
    const topSpacer = document.createElement('div');
    topSpacer.className = 'virtual-spacer top';
    const viewport = document.createElement('div');
    viewport.className = 'virtual-viewport';
    const bottomSpacer = document.createElement('div');
    bottomSpacer.className = 'virtual-spacer bottom';
    ui.capsuleTable.body.append(topSpacer, viewport, bottomSpacer);
    appState.capsuleVirtual.topSpacer = topSpacer;
    appState.capsuleVirtual.viewport = viewport;
    appState.capsuleVirtual.bottomSpacer = bottomSpacer;
    ui.capsuleTable.body.addEventListener('scroll', updateVirtualCapsuleTable);
  }
  if (!pageItems.length) {
    ui.capsuleTable.empty.hidden = false;
  } else {
    ui.capsuleTable.empty.hidden = true;
  }
  appState.capsuleVirtual.rows = pageItems;
  ui.capsuleTable.body.scrollTop = 0;
  updateVirtualCapsuleTable();
  const pageCount = Math.max(1, Math.ceil(rows.length / appState.capsulePageSize));
  if (appState.capsulePage > pageCount) {
    appState.capsulePage = pageCount;
    return renderCapsuleTable();
  }
  ui.capsuleTable.pageLabel.textContent = `Page ${appState.capsulePage} / ${pageCount}`;
  updateBulkActions();
  renderKPIs('#capsules-kpis', [
    { label: 'Total', value: humanize(appState.capsules.length) },
    { label: 'Selected', value: appState.capsuleSelection.size.toString() },
    { label: 'Success rate', value: '100%' },
    { label: 'Cost today', value: '$0.00' },
  ]);
}

function buildCapsuleRow(capsule) {
  const meta = appState.capsuleMeta.get(capsule.id) || {};
  const row = document.createElement('div');
  row.className = 'table-row';
  row.dataset.id = capsule.id;

  const checkboxCell = document.createElement('div');
  checkboxCell.className = 'table-cell checkbox';
  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.checked = appState.capsuleSelection.has(capsule.id);
  checkbox.addEventListener('change', () => {
    if (checkbox.checked) {
      appState.capsuleSelection.add(capsule.id);
    } else {
      appState.capsuleSelection.delete(capsule.id);
    }
    updateBulkActions();
  });
  checkboxCell.appendChild(checkbox);

  const idCell = document.createElement('div');
  idCell.className = 'table-cell';
  idCell.textContent = capsule.id;

  const tagCell = document.createElement('div');
  tagCell.className = 'table-cell';
  tagCell.textContent = capsule.tag;

  const sourceCell = document.createElement('div');
  sourceCell.className = 'table-cell';
  sourceCell.textContent = meta.source || 'operator';

  const statusCell = document.createElement('div');
  statusCell.className = 'table-cell';
  statusCell.textContent = (meta.status || 'ok').toUpperCase();

  const createdCell = document.createElement('div');
  createdCell.className = 'table-cell';
  createdCell.textContent = new Date(capsule.created_at).toLocaleString();

  const lastRunCell = document.createElement('div');
  lastRunCell.className = 'table-cell';
  lastRunCell.textContent = meta.lastRun ? new Date(meta.lastRun).toLocaleString() : '—';

  const actionsCell = document.createElement('div');
  actionsCell.className = 'table-cell actions';
  const openBtn = document.createElement('button');
  openBtn.className = 'ghost';
  openBtn.textContent = 'View';
  openBtn.addEventListener('click', () => openCapsuleDrawer(capsule.id));
  const simulateBtn = document.createElement('button');
  simulateBtn.className = 'ghost';
  simulateBtn.textContent = 'Simulate';
  simulateBtn.addEventListener('click', () => triggerSimulation(capsule));
  actionsCell.append(openBtn, simulateBtn);

  row.append(
    checkboxCell,
    idCell,
    tagCell,
    sourceCell,
    statusCell,
    createdCell,
    lastRunCell,
    actionsCell,
  );
  return row;
}

function updateVirtualCapsuleTable() {
  const { rowHeight, rows = [], viewport, topSpacer, bottomSpacer } = appState.capsuleVirtual;
  if (!viewport || !topSpacer || !bottomSpacer) return;
  const container = ui.capsuleTable.body;
  const visibleCount = Math.ceil((container.clientHeight || 1) / rowHeight) + 4;
  const startIndex = Math.max(0, Math.floor((container.scrollTop || 0) / rowHeight));
  const endIndex = Math.min(rows.length, startIndex + visibleCount);
  const offsetTop = startIndex * rowHeight;
  const offsetBottom = Math.max(0, (rows.length - endIndex) * rowHeight);
  topSpacer.style.height = `${offsetTop}px`;
  bottomSpacer.style.height = `${offsetBottom}px`;
  viewport.innerHTML = '';
  rows.slice(startIndex, endIndex).forEach((capsule) => {
    viewport.appendChild(buildCapsuleRow(capsule));
  });
}

function updateBulkActions() {
  const count = appState.capsuleSelection.size;
  ui.capsuleTable.bulk.hidden = count === 0;
  ui.capsuleTable.bulkCount.textContent = `${count} selected`;
  ui.capsuleTable.selectAll.checked = count && count === filteredCapsules().length;
}

function openCapsuleDrawer(id) {
  const capsule = appState.capsules.find((item) => item.id === id);
  if (!capsule) return;
  const meta = appState.capsuleMeta.get(id) || {};
  ui.capsuleTable.drawerContent.innerHTML = '';
  const info = [
    ['Tag', capsule.tag],
    ['Source', meta.source || 'operator'],
    ['Status', (meta.status || 'ok').toUpperCase()],
    ['Created', new Date(capsule.created_at).toLocaleString()],
    ['Last run', meta.lastRun ? new Date(meta.lastRun).toLocaleString() : '—'],
  ];
  info.forEach(([label, value]) => {
    const p = document.createElement('p');
    const strong = document.createElement('strong');
    strong.textContent = `${label}: `;
    const span = document.createElement('span');
    span.textContent = value;
    p.append(strong, span);
    ui.capsuleTable.drawerContent.appendChild(p);
  });
  const heading = document.createElement('h4');
  heading.textContent = 'Text';
  const pre = document.createElement('pre');
  pre.textContent = capsule.text || 'No text provided.';
  ui.capsuleTable.drawerContent.append(heading, pre);
  ui.capsuleTable.drawer.setAttribute('aria-hidden', 'false');
}

function closeCapsuleDrawer() {
  ui.capsuleTable.drawer.setAttribute('aria-hidden', 'true');
}

function renderResearch() {
  ui.research.queue.innerHTML = '';
  appState.researchQueue.forEach((item, index) => {
    const card = document.createElement('div');
    card.className = 'research-card';
    const header = document.createElement('header');
    header.innerHTML = `<strong>#${index + 1} • ${item.topic}</strong><span class="badge-chip ${item.status}">${item.status.toUpperCase()}</span>`;
    const body = document.createElement('p');
    body.textContent = item.summary || 'Awaiting exploration.';
    const actionBar = document.createElement('div');
    actionBar.className = 'table-cell actions';
    const runBtn = document.createElement('button');
    runBtn.className = 'ghost';
    runBtn.textContent = 'Run now';
    runBtn.addEventListener('click', () => processResearchItem(item));
    const pinBtn = document.createElement('button');
    pinBtn.className = 'ghost';
    pinBtn.textContent = 'Pin to capsule';
    pinBtn.addEventListener('click', () => pinResearchToCapsule(item));
    actionBar.append(runBtn, pinBtn);
    card.append(header, body, actionBar);
    ui.research.queue.appendChild(card);
  });
  ui.research.history.innerHTML = '';
  appState.researchHistory.slice(-5).reverse().forEach((item) => {
    const card = document.createElement('div');
    card.className = 'research-card';
    card.innerHTML = `<header><strong>${item.topic}</strong><span>${new Date(item.timestamp).toLocaleString()}</span></header><p>${item.summary}</p>`;
    ui.research.history.appendChild(card);
  });
  renderKPIs('#research-kpis', [
    { label: 'Queued', value: appState.researchQueue.length.toString() },
    { label: 'Completed', value: appState.researchHistory.length.toString() },
    { label: 'Auto explore', value: ui.chips.research.checked ? 'On' : 'Off' },
  ]);
}

function renderInsights(insights) {
  appState.insights = insights || [];
  ui.insights.stream.innerHTML = '';
  appState.insights.forEach((insight) => {
    const card = document.createElement('div');
    card.className = 'insight-card';
    const header = document.createElement('header');
    const severity = document.createElement('span');
    severity.className = `badge-chip ${insight.severity || 'success'}`;
    severity.textContent = (insight.severity || 'info').toUpperCase();
    const timestamp = document.createElement('span');
    timestamp.textContent = new Date(insight.timestamp || Date.now()).toLocaleString();
    header.append(severity, timestamp);
    const body = document.createElement('p');
    body.textContent = insight.summary || insight.message;
    const footer = document.createElement('div');
    footer.className = 'table-cell actions';
    const openBtn = document.createElement('button');
    openBtn.className = 'ghost';
    openBtn.textContent = 'Open related items';
    openBtn.addEventListener('click', () => {
      if (insight.related && insight.related.capsule_id) {
        openCapsuleDrawer(insight.related.capsule_id);
      }
    });
    footer.appendChild(openBtn);
    card.append(header, body, footer);
    ui.insights.stream.appendChild(card);
  });
  renderKPIs('#insights-kpis', [
    { label: 'Total', value: appState.insights.length.toString() },
    { label: 'Critical', value: appState.insights.filter((i) => i.severity === 'fail').length.toString() },
    { label: 'Warnings', value: appState.insights.filter((i) => i.severity === 'warn').length.toString() },
  ]);
  updateBadges();
}

function renderSimulations() {
  ui.simulation.list.innerHTML = '';
  appState.simulations.slice(-5).reverse().forEach((run) => {
    const card = document.createElement('div');
    card.className = 'simulation-card';
    const header = document.createElement('header');
    header.innerHTML = `<strong>${run.title}</strong><span>${new Date(run.timestamp).toLocaleString()}</span>`;
    const details = document.createElement('p');
    details.textContent = run.summary;
    const metrics = document.createElement('div');
    metrics.className = 'kpi-strip';
    metrics.innerHTML = `
      <div class="kpi-card"><span class="label">Impact</span><strong>${run.impact}</strong></div>
      <div class="kpi-card"><span class="label">Risk</span><strong>${run.risk}</strong></div>
      <div class="kpi-card"><span class="label">Cost</span><strong>${run.cost}</strong></div>
    `;
    card.append(header, details, metrics);
    ui.simulation.list.appendChild(card);
  });
  renderKPIs('#simulation-kpis', [
    { label: 'Runs', value: appState.simulations.length.toString() },
    { label: 'Avg risk', value: `${average(appState.simulations.map((run) => run.riskScore || 0)).toFixed(2)}` },
    { label: 'Next schedule', value: 'Manual' },
  ]);
}

function average(values) {
  if (!values.length) return 0;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function renderLearning(history) {
  appState.learningHistory = history || [];
  const lastEntry = appState.learningHistory.slice(-1)[0];
  if (lastEntry) {
    ui.learning.lastDelta.textContent = `Δ${Number(lastEntry.delta_score || 0).toFixed(3)}`;
    ui.learning.version.textContent = lastEntry.version;
  }
  drawLearningChart();
  renderKPIs('#learning-kpis', [
    { label: 'Snapshots', value: appState.learningHistory.length.toString() },
    { label: 'Avg Δ', value: average(appState.learningHistory.map((h) => h.delta_score || 0)).toFixed(3) },
    { label: 'Last', value: ui.learning.lastDelta.textContent },
  ]);
  updateBadges();
}

function drawLearningChart() {
  const canvas = ui.learning.chart;
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const width = canvas.width = canvas.clientWidth || 600;
  const height = canvas.height = canvas.clientHeight || 160;
  ctx.clearRect(0, 0, width, height);
  ctx.strokeStyle = '#2a3145';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, height / 2);
  ctx.lineTo(width, height / 2);
  ctx.stroke();
  const history = appState.learningHistory.slice(-appState.learningRange);
  if (!history.length) return;
  const maxDelta = Math.max(...history.map((item) => Math.abs(item.delta_score || 0))) || 1;
  ctx.beginPath();
  ctx.strokeStyle = '#4cc9f0';
  ctx.lineWidth = 2;
  history.forEach((entry, index) => {
    const x = (index / Math.max(1, history.length - 1)) * width;
    const y = height / 2 - ((entry.delta_score || 0) / maxDelta) * (height / 2 - 8);
    if (index === 0) {
      ctx.moveTo(x, y);
    } else {
      ctx.lineTo(x, y);
    }
  });
  ctx.stroke();
}

function renderLogs(text) {
  const lines = text.trim().split('\n').filter(Boolean).slice(-200);
  appState.logsRaw = lines.map((line) => {
    try {
      return JSON.parse(line);
    } catch (error) {
      return { raw: line };
    }
  });
  applyLogFilter();
}

function renderActivity(events = []) {
  appState.activity = events;
  if (!ui.activity.list) return;
  ui.activity.list.innerHTML = '';
  if (!events.length) {
    ui.activity.list.innerHTML = '<div class="empty-state small">No recent activity</div>';
  } else {
    events
      .slice(-50)
      .reverse()
      .forEach((event) => {
        const item = document.createElement('div');
        item.className = 'activity-item';
        const ts = event.ts ? new Date(event.ts).toLocaleTimeString() : '';
        item.innerHTML = `
          <div class="activity-meta"><span class="pill">${event.event || 'event'}</span><span class="muted">${ts}</span></div>
          <div class="activity-body">${event.summary || event.message || ''}</div>
        `;
        ui.activity.list.appendChild(item);
      });
  }
  if (ui.activity.count) {
    ui.activity.count.textContent = events.length.toString();
  }
}

function applyLogFilter() {
  const level = ui.logs.level ? ui.logs.level.value : 'all';
  const entries = (appState.logsRaw || []).filter((entry) => {
    if (!level || level === 'all') return true;
    const event = String(entry.event || entry.level || '').toLowerCase();
    if (level === 'error') return event.includes('error') || event.includes('fail');
    if (level === 'warn') return event.includes('warn');
    return true;
  });
  appState.logs = entries;
  ui.logs.stream.innerHTML = '';
  entries.forEach((entry) => {
    const card = document.createElement('div');
    card.className = 'log-line';
    const header = document.createElement('header');
    header.innerHTML = `<strong>${entry.event || 'log'}</strong><span>${entry.ts || ''}</span>`;
    const pre = document.createElement('pre');
    pre.textContent = JSON.stringify(entry, null, 2);
    card.append(header, pre);
    ui.logs.stream.appendChild(card);
  });
  renderKPIs('#logs-kpis', [
    { label: 'Events', value: entries.length.toString() },
    { label: 'Errors', value: entries.filter((line) => String(line.event || '').includes('error')).length.toString() },
  ]);
  updateBadges();
}

function recordExport(format, size) {
  const entry = {
    format,
    size,
    timestamp: new Date().toISOString(),
    expiry: new Date(Date.now() + 86400000).toISOString(),
  };
  appState.exports.push(entry);
  renderExports();
}

function renderExports() {
  ui.exports.history.innerHTML = '';
  appState.exports.slice(-5).reverse().forEach((entry) => {
    const card = document.createElement('div');
    card.className = 'export-card';
    card.innerHTML = `
      <header><strong>${entry.format.toUpperCase()}</strong><span>${new Date(entry.timestamp).toLocaleString()}</span></header>
      <p>Size: ${entry.size}</p>
      <p>Expires: ${new Date(entry.expiry).toLocaleString()}</p>
      <button class="ghost">Re-generate</button>
    `;
    ui.exports.history.appendChild(card);
  });
  renderKPIs('#exports-kpis', [
    { label: 'Total exports', value: appState.exports.length.toString() },
    { label: 'Latest', value: appState.exports.slice(-1)[0]?.format?.toUpperCase() || '—' },
  ]);
  updateBadges();
}

async function refreshStatus() {
  try {
    const status = await api.getStatus();
    updateStatus(status);
  } catch (error) {
    toast(`Status error: ${error.error || error.message || error}`, { runId: `status-${Date.now()}` });
  }
}

async function refreshCapsules() {
  try {
    const tag = appState.capsuleFilters.tag;
    const query = appState.capsuleFilters.query;
    ui.capsuleTable.body.classList.add('loading');
    const response = await api.listCapsules({ tag, q: query });
    const capsules = Array.isArray(response.capsules) ? response.capsules : response;
    appState.capsules = capsules.map((capsule) => ({ ...capsule, text: capsule.text || '' }));
    appState.capsules.forEach((capsule) => {
      if (!appState.capsuleMeta.has(capsule.id)) {
        appState.capsuleMeta.set(capsule.id, { source: 'operator', status: 'ok', lastRun: null });
      }
    });
    renderCapsuleTable();
    updateBadges();
  } catch (error) {
    toast(`Capsule load failed: ${error.error || error.message || error}`);
  } finally {
    ui.capsuleTable.body.classList.remove('loading');
  }
}

async function refreshQuickChecks() {
  try {
    const result = await api.runQuickChecks();
    renderQuickChecks(result);
  } catch (error) {
    toast(`Quick checks failed: ${error.error || error.message || error}`);
  }
}

async function refreshInsights() {
  try {
    const result = await api.fetchInsights();
    renderInsights(result.insights || []);
  } catch (error) {
    toast(`Insights failed: ${error.error || error.message || error}`);
  }
}

async function refreshLearning() {
  try {
    const result = await api.fetchLearningHistory();
    renderLearning(result.history || []);
  } catch (error) {
    toast(`Learning history failed: ${error.error || error.message || error}`);
  }
}

async function refreshLogs() {
  try {
    const text = await api.fetchLogs();
    const raw = typeof text === 'string' ? text : JSON.stringify(text, null, 2);
    renderLogs(raw);
  } catch (error) {
    toast(`Log stream failed: ${error.error || error.message || error}`);
  }
}

async function refreshActivity() {
  try {
    const payload = await api.fetchActivity(50);
    renderActivity(payload.events || []);
  } catch (error) {
    toast(`Activity fetch failed: ${error.error || error.message || error}`);
  }
}

function processAutoLoops() {
  Object.values(appState.autoLoops).forEach((interval) => clearInterval(interval));
  appState.autoLoops = {};
  if (ui.chips.research.checked) {
    appState.autoLoops.research = setInterval(() => {
      if (appState.researchQueue.length) {
        processResearchItem(appState.researchQueue[0]);
      }
    }, 60_000);
  }
  if (ui.chips.insights.checked) {
    appState.autoLoops.insights = setInterval(() => refreshInsights(), 120_000);
  }
}

async function triggerSave() {
  const text = buildCapsuleText();
  if (!text.trim()) {
    toast('Instruction is required');
    return;
  }
  const payload = { text, tag: ui.actions.tag.value || 'general' };
  try {
    const { data: saved, requestId } = await api.saveCapsule(payload);
    const meta = {
      source: ui.actions.source.value || 'operator',
      status: 'ok',
      lastRun: null,
    };
    appState.capsuleMeta.set(saved.id, meta);
    appState.capsules.unshift(saved);
    renderCapsuleTable();
    toast('Capsule saved', { runId: saved.id || requestId });
    ui.actions.output.innerHTML = `<div class="badge-chip success">SAVED</div><pre>${JSON.stringify(saved, null, 2)}</pre>`;
    if (ui.chips.analyze.checked) {
      try {
        await triggerAnalyze(text);
      } catch (analysisError) {
        console.warn('Auto analyze failed', analysisError);
      }
    }
    if (ui.chips.simulate.checked) {
      try {
        await triggerSimulation(saved);
      } catch (simulationError) {
        console.warn('Auto simulate failed', simulationError);
      }
    }
    if (ui.chips.learning.checked) {
      try {
        await triggerLearning();
      } catch (learningError) {
        console.warn('Auto learning failed', learningError);
      }
    }
    eventBus.dispatchEvent(
      new CustomEvent('capsule-saved', { detail: { capsule: saved, requestId } }),
    );
    return saved;
  } catch (error) {
    toast(`Save failed: ${error.error || error.message || error}`);
    ui.actions.output.innerHTML = `<div class="badge-chip fail">FAILED</div><pre>${JSON.stringify(error, null, 2)}</pre>`;
    throw error;
  }
}

async function triggerAnalyze(textOverride) {
  const text = textOverride || buildCapsuleText();
  if (!text.trim()) {
    toast('Instruction required for analysis');
    return;
  }
  try {
    const { data: analysis, requestId } = await api.analyzeCapsule({ text });
    ui.actions.output.innerHTML = `<div class="badge-chip success">ANALYZED</div><pre>${JSON.stringify(analysis, null, 2)}</pre>`;
    toast('Analysis complete', { runId: requestId || `analysis-${Date.now()}` });
    eventBus.dispatchEvent(
      new CustomEvent('capsule-analyzed', { detail: { analysis, requestId } }),
    );
    return analysis;
  } catch (error) {
    toast(`Analyze failed: ${error.error || error.message || error}`);
    ui.actions.output.innerHTML = `<div class="badge-chip warn">NEEDS INPUT</div><pre>${JSON.stringify(error, null, 2)}</pre>`;
    throw error;
  }
}

async function triggerSimulation(capsule) {
  const text = capsule?.text || buildCapsuleText();
  if (!text.trim()) {
    toast('Instruction required for simulation');
    return;
  }
  try {
    const { data: simulation, requestId } = await api.simulateCapsule({
      text,
      capsule_id: capsule?.id,
    });
    const summary = {
      title: capsule?.id || 'Ad-hoc simulation',
      summary: (simulation.plan || []).join(' → '),
      impact: `${simulation.expected_impact?.economy ?? 0} economy / ${simulation.expected_impact?.environment ?? 0} env`,
      risk: (simulation.risks || []).join(', ') || 'low',
      riskScore: simulation.risks?.length || 0,
      cost: '$0.00',
      timestamp: new Date().toISOString(),
    };
    appState.simulations.push(summary);
    renderSimulations();
    toast('Simulation complete', { runId: requestId || `sim-${Date.now()}` });
    ui.actions.output.innerHTML = `<div class="badge-chip success">SIMULATED</div><pre>${JSON.stringify(simulation, null, 2)}</pre>`;
    if (capsule?.id) {
      const meta = appState.capsuleMeta.get(capsule.id) || {};
      meta.lastRun = new Date().toISOString();
      meta.status = 'ok';
      appState.capsuleMeta.set(capsule.id, meta);
      renderCapsuleTable();
    }
    eventBus.dispatchEvent(
      new CustomEvent('simulation-completed', { detail: { simulation, requestId } }),
    );
    return simulation;
  } catch (error) {
    toast(`Simulation failed: ${error.error || error.message || error}`);
    ui.actions.output.innerHTML = `<div class="badge-chip fail">FAILED</div><pre>${JSON.stringify(error, null, 2)}</pre>`;
    throw error;
  }
}

async function triggerLearning() {
  try {
    const { data: learning, requestId } = await api.runLearning({
      metrics: { capsules: appState.capsules.length },
    });
    toast('Learning step recorded', { runId: learning.version || requestId });
    await refreshLearning();
    ui.actions.output.innerHTML = `<div class="badge-chip success">LEARNING</div><pre>${JSON.stringify(learning, null, 2)}</pre>`;
    eventBus.dispatchEvent(
      new CustomEvent('learning-step', { detail: { learning, requestId } }),
    );
    return learning;
  } catch (error) {
    toast(`Learning failed: ${error.error || error.message || error}`);
    ui.actions.output.innerHTML = `<div class="badge-chip warn">NEEDS INPUT</div><pre>${JSON.stringify(error, null, 2)}</pre>`;
    throw error;
  }
}

async function triggerUpgrade() {
  const text = buildCapsuleText();
  if (!text.trim()) {
    toast('Instruction required for upgrade proposal');
    return;
  }
  try {
    const { data: upgrade, requestId } = await api.proposeUpgrade({
      text,
      metadata: { source: ui.actions.source.value || 'operator' },
    });
    toast(upgrade.accepted ? 'Upgrade accepted' : 'Upgrade recorded', {
      runId: requestId || `upgrade-${Date.now()}`,
    });
    ui.actions.output.innerHTML = `<div class="badge-chip ${upgrade.accepted ? 'success' : 'warn'}">${upgrade.accepted ? 'ACCEPTED' : 'REVIEW'}</div><pre>${JSON.stringify(upgrade, null, 2)}</pre>`;
    eventBus.dispatchEvent(
      new CustomEvent('upgrade-recorded', { detail: { upgrade, requestId } }),
    );
    return upgrade;
  } catch (error) {
    toast(`Upgrade failed: ${error.error || error.message || error}`);
    ui.actions.output.innerHTML = `<div class="badge-chip fail">FAILED</div><pre>${JSON.stringify(error, null, 2)}</pre>`;
    throw error;
  }
}

async function triggerDryRun() {
  try {
    const result = await triggerSimulation({ text: buildCapsuleText() });
    if (result) {
      ui.actions.output.innerHTML = `<div class="badge-chip success">DRY-RUN</div><pre>${JSON.stringify(result, null, 2)}</pre>`;
    }
    return result;
  } catch (error) {
    throw error;
  }
}

async function processResearchItem(item) {
  if (!item) return;
  item.status = 'running';
  renderResearch();
  try {
    const { data: research, requestId } = await api.runResearch({ query: item.topic });
    item.status = 'done';
    const entries = research.results || research.insights || [];
    item.summary = entries.map((entry) => entry.summary || entry.title || '').join('\n');
    appState.researchHistory.push({ topic: item.topic, summary: item.summary, timestamp: new Date().toISOString() });
    appState.researchQueue = appState.researchQueue.filter((queueItem) => queueItem !== item);
    renderResearch();
    toast(`Research complete for ${item.topic}`, { runId: requestId || `research-${Date.now()}` });
    eventBus.dispatchEvent(
      new CustomEvent('research-completed', { detail: { research, requestId } }),
    );
  } catch (error) {
    item.status = 'fail';
    item.summary = error.error || error.message || 'Failed';
    renderResearch();
    toast(`Research failed: ${item.summary}`);
  }
}

function pinResearchToCapsule(item) {
  ui.actions.context.value = `${ui.actions.context.value}\n\nPinned research:\n${item.summary}`.trim();
  setContext(ui.actions.context.value);
  toast(`Pinned research from ${item.topic}`);
}

function storePreset() {
  const preset = {
    id: `preset-${Date.now()}`,
    instruction: ui.actions.instruction.value,
    context: ui.actions.context.value,
    tag: ui.actions.tag.value,
  };
  appState.presets = [...appState.presets.slice(-9), preset];
  renderPresets();
  writePresets(appState.presets);
  toast('Preset saved', { runId: preset.id });
}

function renderPresets() {
  ui.actions.presetList.innerHTML = '';
  appState.presets.slice(-10).reverse().forEach((preset) => {
    const li = document.createElement('li');
    li.textContent = preset.instruction.slice(0, 60) || 'Untitled preset';
    li.addEventListener('click', () => {
      ui.actions.instruction.value = preset.instruction;
      ui.actions.context.value = preset.context;
      ui.actions.tag.value = preset.tag;
      setInstruction(preset.instruction);
      setContext(preset.context);
    });
    ui.actions.presetList.appendChild(li);
  });
  renderActionKPIs();
}

function openCommandPalette() {
  ui.commandPalette.root.setAttribute('aria-hidden', 'false');
  ui.commandPalette.input.value = '';
  ui.commandPalette.input.focus();
  populateCommandResults('');
}

function closeCommandPalette() {
  ui.commandPalette.root.setAttribute('aria-hidden', 'true');
}

function populateCommandResults(term) {
  const results = [];
  const query = term.trim().toLowerCase();
  appState.capsules.forEach((capsule) => {
    if (!query || capsule.id.toLowerCase().includes(query) || capsule.text.toLowerCase().includes(query)) {
      results.push({ label: `Capsule ${capsule.id}`, action: () => openCapsuleDrawer(capsule.id) });
    }
  });
  appState.logs.forEach((entry) => {
    if (!entry.event) return;
    if (!query || entry.event.toLowerCase().includes(query)) {
      results.push({ label: `Log • ${entry.event}`, action: () => setActiveSection('section-logs') });
    }
  });
  if (!results.length) {
    ui.commandPalette.results.innerHTML = '<div class="command-item">No results</div>';
    return;
  }
  ui.commandPalette.results.innerHTML = '';
  results.slice(0, 20).forEach((result, index) => {
    const item = document.createElement('div');
    item.className = 'command-item';
    item.textContent = result.label;
    item.dataset.index = index;
    item.tabIndex = 0;
    item.addEventListener('click', () => {
      result.action();
      closeCommandPalette();
    });
    ui.commandPalette.results.appendChild(item);
  });
}

function setActiveSection(sectionId) {
  appState.activeSection = sectionId;
  ui.navItems.forEach((item) => {
    const target = item.dataset.target;
    const isActive = target === sectionId;
    item.classList.toggle('active', isActive);
    const section = document.getElementById(target);
    if (section) section.classList.toggle('active', isActive);
  });
}

function setButtonBusy(button, busy) {
  if (!button) return;
  button.classList.toggle('is-loading', busy);
  button.disabled = busy;
  if (busy) {
    button.setAttribute('aria-busy', 'true');
  } else {
    button.removeAttribute('aria-busy');
  }
}

function bindAction(button, handler) {
  if (!button) return;
  button.addEventListener('click', async () => {
    if (button.disabled) return;
    setButtonBusy(button, true);
    button.classList.remove('state-success', 'state-error');
    try {
      await handler();
      button.classList.add('state-success');
      setTimeout(() => button.classList.remove('state-success'), 1200);
    } catch (error) {
      button.classList.add('state-error');
      setTimeout(() => button.classList.remove('state-error'), 1600);
    } finally {
      setButtonBusy(button, false);
    }
  });
}

function initEvents() {
  ui.actions.instruction.addEventListener('input', (event) => setInstruction(event.target.value));
  ui.actions.context.addEventListener('input', (event) => setContext(event.target.value));
  ui.capsuleTable.filterQuery.addEventListener('input', (event) => {
    appState.capsuleFilters.query = event.target.value;
    refreshCapsules();
  });
  ui.capsuleTable.filterTag.addEventListener('input', (event) => {
    appState.capsuleFilters.tag = event.target.value;
    refreshCapsules();
  });
  ui.capsuleTable.filterStatus.addEventListener('change', (event) => {
    appState.capsuleFilters.status = event.target.value;
    renderCapsuleTable();
  });
  ui.capsuleTable.quickFilters.forEach((button) => {
    button.addEventListener('click', () => {
      const filter = button.dataset.filter;
      if (filter === 'errors') appState.capsuleFilters.status = 'error';
      if (filter === 'warnings') appState.capsuleFilters.status = 'warn';
      if (filter === 'recent') appState.capsulePage = 1;
      if (filter === 'mine') appState.capsuleFilters.tag = ui.actions.tag.value || '';
      if (filter === 'recent') {
        appState.capsules.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
      }
      if (filter === 'errors') ui.capsuleTable.filterStatus.value = 'error';
      if (filter === 'warnings') ui.capsuleTable.filterStatus.value = 'warn';
      if (filter === 'mine') ui.capsuleTable.filterTag.value = ui.actions.tag.value;
      renderCapsuleTable();
    });
  });
  ui.capsuleTable.prev.addEventListener('click', () => {
    appState.capsulePage = Math.max(1, appState.capsulePage - 1);
    renderCapsuleTable();
  });
  ui.capsuleTable.next.addEventListener('click', () => {
    const rows = filteredCapsules();
    const pageCount = Math.max(1, Math.ceil(rows.length / appState.capsulePageSize));
    appState.capsulePage = Math.min(pageCount, appState.capsulePage + 1);
    renderCapsuleTable();
  });
  ui.capsuleTable.selectAll.addEventListener('change', (event) => {
    if (event.target.checked) {
      filteredCapsules().forEach((capsule) => appState.capsuleSelection.add(capsule.id));
    } else {
      appState.capsuleSelection.clear();
    }
    renderCapsuleTable();
  });
  ui.capsuleTable.bulkDelete.addEventListener('click', async () => {
    const ids = Array.from(appState.capsuleSelection);
    if (!ids.length) return;
    const token = prompt(`Enter admin token to delete ${ids.length} capsule(s)`);
    if (!token) {
      toast('Deletion canceled.');
      return;
    }
    try {
      const { data, requestId } = await api.deleteCapsules(ids, token);
      const count = data.count || data.deleted?.length || 0;
      toast(`Deleted ${count} capsule(s).`, { runId: requestId || `delete-${Date.now()}` });
      appState.capsuleSelection.clear();
      await refreshCapsules();
    } catch (error) {
      toast(`Delete failed: ${error.error || error.message || error}`, {
        runId: error.requestId || `delete-error-${Date.now()}`,
      });
    }
  });
  ui.capsuleTable.drawerClose.addEventListener('click', closeCapsuleDrawer);
  ui.research.form.addEventListener('submit', (event) => {
    event.preventDefault();
    const topic = ui.research.input.value.trim();
    if (!topic) return;
    appState.researchQueue.push({ topic, status: 'queued', summary: '' });
    ui.research.input.value = '';
    renderResearch();
    if (!ui.chips.research.checked) processResearchItem(appState.researchQueue[0]);
  });
  ui.commandPalette.input.addEventListener('input', (event) => populateCommandResults(event.target.value));
  ui.commandPalette.close.addEventListener('click', closeCommandPalette);
  bindAction(ui.primaryActions.save, () => triggerSave());
  bindAction(ui.primaryActions.analyze, () => triggerAnalyze());
  bindAction(ui.primaryActions.simulate, () => triggerSimulation());
  bindAction(ui.primaryActions.learning, () => triggerLearning());
  bindAction(ui.primaryActions.upgrade, () => triggerUpgrade());
  bindAction(ui.primaryActions.dryrun, () => triggerDryRun());
  $('#preset-save').addEventListener('click', storePreset);
  const chipLabels = {
    analyze: 'Auto Analyze',
    simulate: 'Auto Simulate',
    learning: 'Auto Learning',
    research: 'Auto Explore',
    insights: 'Auto Reflect',
  };
  Object.entries(ui.chips).forEach(([key, chip]) => {
    if (!chip) return;
    chip.addEventListener('change', async (event) => {
      const value = event.target.checked;
      chip.disabled = true;
      chip.setAttribute('aria-busy', 'true');
      try {
        await settingsStore.setToggle(key, value);
        renderActionKPIs();
        processAutoLoops();
        toast(`${chipLabels[key] || key} ${value ? 'enabled' : 'disabled'}`, {
          runId: `toggle-${key}-${Date.now()}`,
        });
      } catch (error) {
        chip.checked = !value;
        toast(`Toggle update failed: ${error.error || error.message || error}`, {
          runId: `toggle-${key}-${Date.now()}`,
        });
      } finally {
        chip.disabled = false;
        chip.removeAttribute('aria-busy');
      }
    });
  });
  ui.refreshButton.addEventListener('click', async () => {
    await refreshStatus();
    await refreshActivity();
    await refreshCapsules();
  });
  ui.autoRefresh.addEventListener('change', async (event) => {
    const value = event.target.checked;
    ui.autoRefresh.disabled = true;
    try {
      await settingsStore.setAutoRefresh(value);
      toast(`Auto refresh ${value ? 'enabled' : 'paused'}`, {
        runId: `refresh-${Date.now()}`,
      });
    } catch (error) {
      event.target.checked = !value;
      toast(`Auto refresh update failed: ${error.error || error.message || error}`);
    } finally {
      ui.autoRefresh.disabled = false;
    }
  });
  ui.quickChecks.toggle.addEventListener('click', () => {
    const expanded = ui.quickChecks.toggle.getAttribute('aria-expanded') === 'true';
    ui.quickChecks.toggle.setAttribute('aria-expanded', String(!expanded));
    ui.quickChecks.details.hidden = expanded;
  });
  ui.quickChecks.rerun.addEventListener('click', refreshQuickChecks);
  ui.killSwitch.addEventListener('click', async () => {
    const token = prompt('Enter admin token to engage kill switch');
    if (!token) return;
    try {
      const { data, requestId } = await api.killSwitch(token);
      toast(`Kill switch ${data.status}`, { runId: requestId || `kill-${Date.now()}` });
      eventBus.dispatchEvent(
        new CustomEvent('kill-switch-engaged', { detail: { status: data.status, requestId } }),
      );
      await refreshStatus();
    } catch (error) {
      toast(`Kill switch failed: ${error.error || error.message || error}`);
    }
  });
  ui.exports.buttons.forEach((button) => {
    button.addEventListener('click', async () => {
      const format = button.dataset.format;
      try {
        const blob = await api.exportCapsules(format);
        recordExport(format, `${(blob.size / 1024).toFixed(1)} KB`);
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = `capsules.${format}`;
        link.click();
        toast(`Exported ${format.toUpperCase()} capsules`);
      } catch (error) {
        toast(`Export failed: ${error.error || error.message || error}`);
      }
    });
  });
  const importBtn = $('#capsule-import');
  if (importBtn) {
    importBtn.addEventListener('click', () => toast('Import coming soon'));
  }
  ui.learning.ranges.forEach((button) => {
    button.addEventListener('click', () => {
      ui.learning.ranges.forEach((btn) => btn.classList.toggle('active', btn === button));
      appState.learningRange = Number(button.dataset.range || 30);
      drawLearningChart();
    });
  });
  ui.navItems.forEach((item) => item.addEventListener('click', () => setActiveSection(item.dataset.target)));
  if (ui.logs.level) {
    ui.logs.level.addEventListener('change', applyLogFilter);
  }
  if (ui.helpButton) {
    ui.helpButton.addEventListener('click', () => toast('See README.md for operator handbook.'));
  }
  document.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      openCommandPalette();
    }
    if (event.key === '/' && !['INPUT', 'TEXTAREA'].includes(event.target.tagName)) {
      event.preventDefault();
      const activeSearch = $('#' + appState.activeSection + ' input[type="search"]');
      if (activeSearch) activeSearch.focus();
    }
    if (event.key === '?' && !['INPUT', 'TEXTAREA'].includes(event.target.tagName)) {
      event.preventDefault();
      toast('Help center coming soon');
    }
    if (event.key === 'Escape' && ui.commandPalette.root.getAttribute('aria-hidden') === 'false') {
      closeCommandPalette();
    }
    if (event.key === 'Escape' && ui.capsuleTable.drawer.getAttribute('aria-hidden') === 'false') {
      closeCapsuleDrawer();
    }
  });
  let gPress = 0;
  document.addEventListener('keydown', (event) => {
    if (event.key.toLowerCase() === 'g') {
      const now = Date.now();
      if (now - gPress < 300) {
        setActiveSection('section-actions');
        gPress = 0;
      } else {
        gPress = now;
      }
    }
  });
}

async function bootstrap() {
  hydratePresets();
  if (ui.loading) ui.loading.hidden = false;
  document.body.classList.add('is-hydrating');
  await settingsStore.hydrate();
  initEvents();
  processAutoLoops();
  await refreshStatus();
  await refreshActivity();
  await refreshCapsules();
  await refreshQuickChecks();
  await refreshInsights();
  await refreshLearning();
  await refreshLogs();
  renderResearch();
  renderSimulations();
  renderExports();
  renderPresets();
  setInstruction('');
  setContext('');
  document.body.classList.remove('is-hydrating');
  if (ui.loading) ui.loading.hidden = true;
  manageStatusPolling(appState.autoRefresh);
}

window.GaiaDashboard = {
  getStatus: api.getStatus,
  runQuickChecks: api.runQuickChecks,
  listCapsules(params = {}) {
    const page = params.page || 1;
    const pageSize = params.pageSize || appState.capsulePageSize;
    const items = appState.capsules.filter((capsule) => {
      if (params.tag && capsule.tag !== params.tag) return false;
      if (params.q && !capsule.text.toLowerCase().includes(params.q.toLowerCase())) return false;
      if (params.status) {
        const meta = appState.capsuleMeta.get(capsule.id);
        if (!meta || meta.status !== params.status) return false;
      }
      return true;
    });
    const offset = (page - 1) * pageSize;
    return items.slice(offset, offset + pageSize);
  },
  getCapsule(id) {
    return appState.capsules.find((capsule) => capsule.id === id);
  },
  listActivity(params = {}) {
    const limit = params.limit || 50;
    return appState.activity.slice(-limit);
  },
  fetchActivity: api.fetchActivity,
  autonomyStatus: api.autonomyStatus,
  autonomyRunOnce: api.autonomyRunOnce,
  runCapsule(id, options = {}) {
    const capsule = appState.capsules.find((item) => item.id === id);
    if (!capsule) return Promise.reject(new Error('Capsule not found'));
    if (options.dryRun) {
      return api.simulateCapsule({ text: capsule.text, capsule_id: capsule.id }).then((result) => result.data);
    }
    return triggerSimulation(capsule);
  },
  listSimulations() {
    return [...appState.simulations];
  },
  startSimulation(params) {
    return api.simulateCapsule(params).then((result) => result.data);
  },
  listInsights(params = {}) {
    const severity = params.severity;
    if (!severity) return [...appState.insights];
    return appState.insights.filter((item) => item.severity === severity);
  },
  listLogs(params = {}) {
    const level = params.level || 'all';
    if (level === 'all') return [...(appState.logsRaw || [])];
    return (appState.logsRaw || []).filter((entry) => {
      const event = String(entry.event || entry.level || '').toLowerCase();
      if (level === 'error') return event.includes('error') || event.includes('fail');
      if (level === 'warn') return event.includes('warn');
      return true;
    });
  },
  exportData(params = {}) {
    const type = params.type || 'json';
    return api.exportCapsules(type);
  },
  bus: eventBus,
};

eventBus.addEventListener('capsule-saved', async () => {
  await refreshStatus();
  await refreshQuickChecks();
});

eventBus.addEventListener('simulation-completed', async () => {
  await refreshStatus();
  await refreshQuickChecks();
});

eventBus.addEventListener('learning-step', async () => {
  await refreshStatus();
  await refreshLearning();
});

eventBus.addEventListener('research-completed', async () => {
  await refreshInsights();
});

eventBus.addEventListener('upgrade-recorded', async () => {
  await refreshStatus();
});

eventBus.addEventListener('kill-switch-engaged', async () => {
  await refreshStatus();
  await refreshQuickChecks();
});

document.addEventListener('DOMContentLoaded', bootstrap);
