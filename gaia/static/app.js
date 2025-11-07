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
  logOutput: $('#log-output'),
  capsuleList: $('#capsule-list'),
  actionsOutput: $('#actions-output'),
  simulationOutput: $('#simulation-output'),
};

const state = {
  learningTrend: [],
};

function setOutput(el, data) {
  if (!el) return;
  if (typeof data === 'string') {
    el.textContent = data;
  } else {
    el.textContent = JSON.stringify(data, null, 2);
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

async function analyzeCapsule() {
  const text = $('#capsule-text').value.trim();
  if (!text) {
    setOutput(statusEls.actionsOutput, 'Enter text to analyze.');
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

async function simulateCapsule() {
  const text = $('#capsule-text').value.trim();
  if (!text) {
    setOutput(statusEls.simulationOutput, 'Provide text to simulate.');
    return;
  }
  try {
    const data = await fetchJSON('/simulate/run', {
      method: 'POST',
      body: JSON.stringify({ text }),
    });
    setOutput(statusEls.simulationOutput, data);
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

async function runLearningStep() {
  try {
    const data = await fetchJSON('/learning/step', {
      method: 'POST',
      body: JSON.stringify({
        engagement: Math.random() * 10,
        success_rate: 0.8 + Math.random() * 0.2,
        feedback_score: 0.6 + Math.random() * 0.4,
      }),
    });
    statusEls.learningVersion.textContent = data.version;
    statusEls.learningDelta.textContent = data.delta_score;
    state.learningTrend.push(data.delta_score);
    if (state.learningTrend.length > 30) {
      state.learningTrend.shift();
    }
    drawTrend();
    setOutput(statusEls.actionsOutput, data);
    await refreshStatus();
  } catch (error) {
    setOutput(statusEls.actionsOutput, error);
  }
}

function drawTrend() {
  const canvas = $('#learning-trend');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);
  if (state.learningTrend.length < 2) {
    ctx.fillStyle = '#94a3b8';
    ctx.fillText('Trend will appear after multiple learning steps.', 10, height / 2);
    return;
  }
  const max = Math.max(...state.learningTrend);
  const min = Math.min(...state.learningTrend);
  const range = Math.max(0.01, max - min);
  ctx.strokeStyle = '#2f80ed';
  ctx.lineWidth = 2;
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
  $('#btn-analyze').addEventListener('click', analyzeCapsule);
  $('#btn-simulate').addEventListener('click', simulateCapsule);
  $('#btn-upgrade').addEventListener('click', proposeUpgrade);
  $('#btn-learning').addEventListener('click', runLearningStep);
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
}

async function boot() {
  initTabs();
  initDarkMode();
  initEvents();
  await refreshStatus();
  await refreshCapsules();
  await refreshLogs();
  setInterval(refreshStatus, 7000);
  setInterval(refreshLogs, 10000);
}

document.addEventListener('DOMContentLoaded', boot);
