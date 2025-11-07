const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => document.querySelectorAll(selector);

const statusEls = {
  uptime: $('#status-uptime'),
  version: $('#status-version'),
  calls: $('#status-calls'),
  capsules: $('#status-capsules'),
  avg: $('#status-avg'),
  last: $('#status-last'),
  halted: $('#halted-banner'),
  learningVersion: $('#learning-version'),
  learningDelta: $('#learning-delta'),
  learningTrend: $('#learning-trend'),
  logOutput: $('#log-output'),
  capsuleList: $('#capsule-list'),
  actionsOutput: $('#actions-output'),
  simulationOutput: $('#simulation-output'),
  researchOutput: $('#research-output'),
};

const state = {
  learningTrend: [],
  autoIntervals: {
    analyze: null,
    simulate: null,
    learning: null,
  },
  lastLearningDelta: null,
};

const autoControls = {
  analyze: $('#auto-analyze'),
  simulate: $('#auto-simulate'),
  learning: $('#auto-learning'),
};

function setOutput(el, data) {
  if (!el) return;
  if (typeof data === 'string') {
    el.textContent = data;
  } else {
    el.textContent = JSON.stringify(data, null, 2);
  }
}

function updateLearningMetrics(version, delta, { track = false } = {}) {
  if (statusEls.learningVersion) {
    statusEls.learningVersion.textContent = version || '-';
  }
  if (statusEls.learningDelta) {
    statusEls.learningDelta.textContent =
      typeof delta === 'number' ? delta.toFixed(3) : '-';
  }
  if (typeof delta === 'number') {
    state.lastLearningDelta = delta;
  }
  if (track && typeof delta === 'number') {
    state.learningTrend.push(delta);
    if (state.learningTrend.length > 50) {
      state.learningTrend.shift();
    }
    drawTrend();
  }
}

async function fetchJSON(url, options = {}) {
  const response = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  const text = await response.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch (err) {
    data = { raw: text };
  }
  if (!response.ok) {
    throw data;
  }
  return data;
}

async function refreshStatus() {
  try {
    const data = await fetchJSON('/status', { method: 'GET' });
    statusEls.uptime.textContent = `${data.uptime_s}s`;
    statusEls.version.textContent = data.version || '-';
    statusEls.calls.textContent = data.api_calls ?? 0;
    statusEls.capsules.textContent = data.capsules_processed ?? 0;
    statusEls.avg.textContent = data.processing_ms_avg ?? 0;
    statusEls.last.textContent = data.last_action || '-';
    statusEls.halted.classList.toggle('hidden', !data.halted);
    const delta = typeof data.learning_delta === 'number' ? data.learning_delta : null;
    const shouldTrack =
      delta !== null && (state.learningTrend.length === 0 || state.learningTrend[state.learningTrend.length - 1] !== delta);
    updateLearningMetrics(data.learning_version, delta, { track: shouldTrack });
  } catch (error) {
    console.error('Status error', error);
  }
}

async function saveCapsule() {
  const text = $('#capsule-text').value.trim();
  const tag = $('#capsule-tag').value.trim() || 'general';
  if (!text) {
    setOutput(statusEls.actionsOutput, 'Provide text before saving.');
    return;
  }
  try {
    const data = await fetchJSON('/capsules/save', {
      method: 'POST',
      body: JSON.stringify({ text, tag }),
    });
    setOutput(statusEls.actionsOutput, data);
    await refreshCapsules();
    await refreshStatus();
  } catch (error) {
    setOutput(statusEls.actionsOutput, error);
  }
}

async function analyzeCapsule({ silent = false } = {}) {
  const text = $('#capsule-text').value.trim();
  if (!text) {
    if (!silent) {
      setOutput(statusEls.actionsOutput, 'Enter text to analyze.');
    }
    return;
  }
  try {
    const data = await fetchJSON('/capsules/analyze', {
      method: 'POST',
      body: JSON.stringify({ text }),
    });
    setOutput(statusEls.actionsOutput, data);
  } catch (error) {
    setOutput(statusEls.actionsOutput, error);
  }
}

async function simulateCapsule({ silent = false } = {}) {
  const text = $('#capsule-text').value.trim();
  if (!text) {
    if (!silent) {
      setOutput(statusEls.simulationOutput, 'Provide text to simulate.');
    }
    return;
  }
  try {
    const data = await fetchJSON('/simulate/run', {
      method: 'POST',
      body: JSON.stringify({ text }),
    });
    setOutput(statusEls.simulationOutput, data);
    await refreshStatus();
  } catch (error) {
    setOutput(statusEls.simulationOutput, error);
  }
}

async function proposeUpgrade() {
  const text = $('#capsule-text').value.trim();
  if (!text) {
    setOutput(statusEls.actionsOutput, 'Enter a proposal to submit.');
    return;
  }
  try {
    const data = await fetchJSON('/upgrades/propose', {
      method: 'POST',
      body: JSON.stringify({ proposal: text, rationale: 'Submitted via dashboard' }),
    });
    setOutput(statusEls.actionsOutput, data);
  } catch (error) {
    setOutput(statusEls.actionsOutput, error);
  }
}

async function runLearningStep({ silent = false } = {}) {
  try {
    const data = await fetchJSON('/learning/step', {
      method: 'POST',
      body: JSON.stringify({
        engagement: Math.random() * 10,
        success_rate: 0.8 + Math.random() * 0.2,
        feedback_score: 0.6 + Math.random() * 0.4,
      }),
    });
    const delta = Number(data.delta_score);
    updateLearningMetrics(data.version, delta, { track: true });
    if (!silent) {
      setOutput(statusEls.actionsOutput, data);
    }
    await refreshStatus();
  } catch (error) {
    setOutput(statusEls.actionsOutput, error);
  }
}

function drawTrend() {
  const canvas = statusEls.learningTrend;
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  const ratio = window.devicePixelRatio || 1;
  const width = (canvas.clientWidth || 320) * ratio;
  const height = (canvas.clientHeight || 140) * ratio;
  canvas.width = width;
  canvas.height = height;
  ctx.clearRect(0, 0, width, height);
  if (state.learningTrend.length < 2) {
    ctx.fillStyle = '#94a3b8';
    ctx.font = `${14 * ratio}px Inter, sans-serif`;
    ctx.fillText('Trend appears after multiple learning steps.', 12 * ratio, height / 2);
    return;
  }
  const max = Math.max(...state.learningTrend);
  const min = Math.min(...state.learningTrend);
  const range = Math.max(0.01, max - min);
  ctx.strokeStyle = '#2f80ed';
  ctx.lineWidth = 2 * ratio;
  ctx.beginPath();
  state.learningTrend.forEach((value, index) => {
    const x = (index / (state.learningTrend.length - 1)) * width;
    const y = height - ((value - min) / range) * height;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

async function refreshCapsules(params = {}) {
  const url = new URL('/capsules/list', window.location.origin);
  if (params.tag) url.searchParams.set('tag', params.tag);
  if (params.query) url.searchParams.set('q', params.query);
  try {
    const data = await fetchJSON(url.toString(), { method: 'GET' });
    statusEls.capsuleList.innerHTML = '';
    (data.capsules || []).forEach((capsule) => {
      const li = document.createElement('li');
      li.innerHTML = `
        <span class="tag">${capsule.tag || 'untagged'}</span>
        <div>${capsule.text}</div>
        <small>${capsule.created_at}</small>
      `;
      li.addEventListener('click', () => {
        $('#capsule-text').value = capsule.text;
        $('#capsule-tag').value = capsule.tag || '';
      });
      statusEls.capsuleList.appendChild(li);
    });
  } catch (error) {
    statusEls.capsuleList.innerHTML = `<li class="error">${JSON.stringify(error)}</li>`;
  }
}

async function refreshLogs() {
  try {
    const response = await fetch('/export/logs');
    const text = await response.text();
    statusEls.logOutput.textContent = text.trim();
  } catch (error) {
    console.error('Log fetch failed', error);
  }
}

async function exploreResearch({ silent = false } = {}) {
  const input = $('#research-query');
  const topic = input ? input.value.trim() : '';
  if (!topic) {
    if (!silent) {
      setOutput(statusEls.researchOutput, 'Enter a topic to explore.');
    }
    return;
  }
  try {
    const data = await fetchJSON(`/research/explore?q=${encodeURIComponent(topic)}`);
    setOutput(statusEls.researchOutput, data);
    await refreshStatus();
  } catch (error) {
    setOutput(statusEls.researchOutput, error);
  }
}

function setAutoAction(key, enabled, intervalMs, handler) {
  if (state.autoIntervals[key]) {
    clearInterval(state.autoIntervals[key]);
    state.autoIntervals[key] = null;
  }
  if (enabled) {
    handler({ silent: true });
    state.autoIntervals[key] = setInterval(() => handler({ silent: true }), intervalMs);
  }
}

function initTabs() {
  $$('.tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      $$('.tab').forEach((t) => t.classList.remove('active'));
      $$('.panel').forEach((panel) => panel.classList.remove('active'));
      tab.classList.add('active');
      const target = document.getElementById(tab.dataset.target);
      if (target) target.classList.add('active');
    });
  });
}

function initDarkMode() {
  const toggle = $('#dark-mode-toggle');
  const saved = localStorage.getItem('gaia-dark-mode');
  if (saved === 'true') {
    document.body.classList.add('dark-mode');
    toggle.checked = true;
  }
  toggle.addEventListener('change', () => {
    document.body.classList.toggle('dark-mode', toggle.checked);
    localStorage.setItem('gaia-dark-mode', toggle.checked);
  });
}

function initEvents() {
  $('#refresh-status').addEventListener('click', refreshStatus);
  $('#btn-save').addEventListener('click', saveCapsule);
  $('#btn-analyze').addEventListener('click', () => analyzeCapsule());
  $('#btn-simulate').addEventListener('click', () => simulateCapsule());
  $('#btn-upgrade').addEventListener('click', proposeUpgrade);
  $('#btn-learning').addEventListener('click', () => runLearningStep());
  const researchButton = $('#btn-research');
  if (researchButton) {
    researchButton.addEventListener('click', () => exploreResearch());
  }
  $('#btn-filter').addEventListener('click', () => {
    refreshCapsules({
      tag: $('#filter-tag').value.trim(),
      query: $('#filter-query').value.trim(),
    });
  });
  $('#btn-refresh-capsules').addEventListener('click', () => refreshCapsules());
  $('#btn-download-logs').addEventListener('click', () => {
    window.open('/export/logs', '_blank');
  });
  $$('.export-btn').forEach((button) => {
    button.addEventListener('click', () => {
      window.open(`/export/capsules?fmt=${button.dataset.fmt}`, '_blank');
    });
  });

  if (autoControls.analyze) {
    autoControls.analyze.addEventListener('change', () =>
      setAutoAction('analyze', autoControls.analyze.checked, 20000, analyzeCapsule),
    );
  }
  if (autoControls.simulate) {
    autoControls.simulate.addEventListener('change', () =>
      setAutoAction('simulate', autoControls.simulate.checked, 30000, simulateCapsule),
    );
  }
  if (autoControls.learning) {
    autoControls.learning.addEventListener('change', () =>
      setAutoAction('learning', autoControls.learning.checked, 45000, runLearningStep),
    );
  }
}

async function boot() {
  initTabs();
  initDarkMode();
  initEvents();
  await refreshStatus();
  await refreshCapsules();
  await refreshLogs();
  drawTrend();
  setInterval(refreshStatus, 7000);
  setInterval(refreshLogs, 10000);
  setInterval(() => refreshCapsules(), 15000);
}

document.addEventListener('DOMContentLoaded', boot);
