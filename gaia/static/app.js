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
  insightsSummary: $('#insights-summary'),
  insightsOutput: $('#insights-output'),
  insightsNext: $('#insights-next'),
};

const state = {
  learningTrend: [],
  currentCapsule: null,
  autoIntervals: {
    analyze: null,
    simulate: null,
    learning: null,
    research: null,
    insights: null,
  },
  lastLearningDelta: null,
  researchTopics: [
    'sustainable energy',
    'circular economy',
    'climate resilience',
    'green infrastructure',
    'eco-innovation',
  ],
  researchIndex: 0,
};

const autoControls = {
  analyze: $('#auto-analyze'),
  simulate: $('#auto-simulate'),
  learning: $('#auto-learning'),
  research: $('#auto-research'),
  insights: $('#auto-insights'),
};

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function renderJSON(el, data) {
  if (!el) return;
  el.classList.remove('placeholder');
  if (typeof data === 'string') {
    el.innerHTML = `<p>${escapeHtml(data)}</p>`;
    return;
  }
  const formatted = escapeHtml(JSON.stringify(data, null, 2));
  el.innerHTML = `<pre>${formatted}</pre>`;
}

function renderSimulation(data) {
  const el = statusEls.simulationOutput;
  if (!el) return;
  el.classList.remove('placeholder');
  if (!data || data.error) {
    renderJSON(el, data || { error: 'Simulation failed' });
    return;
  }
  const plan = Array.isArray(data.plan) ? data.plan : [];
  const risks = Array.isArray(data.risks) ? data.risks : [];
  const impact = data.expected_impact || {};
  const impactKeys = Object.keys(impact);
  el.innerHTML = `
    <div class="sim-grid">
      <div class="sim-card">
        <h3>Plan</h3>
        ${plan.length ? `<ol>${plan.map((step) => `<li>${escapeHtml(step)}</li>`).join('')}</ol>` : '<p class="muted">No plan generated.</p>'}
      </div>
      <div class="sim-card">
        <h3>Risks</h3>
        ${risks.length ? `<ul>${risks.map((risk) => `<li>${escapeHtml(risk)}</li>`).join('')}</ul>` : '<p class="muted">No significant risks detected.</p>'}
      </div>
      <div class="sim-card">
        <h3>Expected Impact</h3>
        ${impactKeys.length
          ? `<div class="sim-impact">${impactKeys
              .map(
                (key) => `
                  <div>
                    <span class="muted">${escapeHtml(key)}</span>
                    <strong>${escapeHtml(impact[key])}</strong>
                  </div>
                `,
              )
              .join('')}</div>`
          : '<p class="muted">Impact assessment pending.</p>'}
      </div>
    </div>
  `;
}

function renderResearch(data) {
  const el = statusEls.researchOutput;
  if (!el) return;
  if (!data || data.error) {
    renderJSON(el, data || { error: 'Unable to explore research.' });
    return;
  }
  const results = Array.isArray(data.results) ? data.results : [];
  if (!results.length) {
    el.innerHTML = '<p class="muted">No research highlights available yet.</p>';
    return;
  }
  el.innerHTML = `
    <div class="research-results">
      ${results
        .map(
          (item) => `
            <article class="research-card">
              <h3>${escapeHtml(item.title)}</h3>
              <p class="research-summary">${escapeHtml(item.summary || 'Summary unavailable.')}</p>
              ${item.url ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener">View source</a>` : ''}
            </article>
          `,
        )
        .join('')}
    </div>
    <p class="muted">Source: ${escapeHtml(data.source || 'curated')}</p>
  `;
}

function renderInsights(data) {
  const summaryEl = statusEls.insightsSummary;
  const grid = statusEls.insightsOutput;
  const nextEl = statusEls.insightsNext;
  if (!grid) return;
  if (!data || data.error) {
    if (summaryEl) summaryEl.textContent = 'Unable to generate insights right now.';
    renderJSON(grid, data || { error: 'Insight generation failed.' });
    if (nextEl) nextEl.textContent = '';
    return;
  }
  if (summaryEl) {
    summaryEl.textContent = data.summary || 'Gaia reflections available.';
  }
  const insights = Array.isArray(data.insights) ? data.insights : [];
  if (!insights.length) {
    grid.innerHTML = '<p class="muted">No insights yet. Capture a capsule to begin.</p>';
  } else {
    grid.innerHTML = insights
      .map((insight) => {
        const indicator = insight.indicator || {};
        const confidence = Math.round((insight.confidence ?? 0) * 100);
        return `
          <div class="insight-card">
            <h3>${escapeHtml(insight.title || 'Insight')}</h3>
            <p class="insight-message">${escapeHtml(insight.message || '')}</p>
            <div class="insight-indicator">
              <div class="meter"><span style="width:${Math.min(100, Math.max(0, confidence))}%"></span></div>
              <span class="insight-confidence">${confidence}% confident</span>
            </div>
            ${indicator.label ? `<small class="muted">${escapeHtml(indicator.label)} • ${escapeHtml(indicator.value ?? '')} ${escapeHtml(indicator.unit ?? '')}</small>` : ''}
          </div>
        `;
      })
      .join('');
  }
  if (nextEl) {
    const next = Array.isArray(data.recommended_next) ? data.recommended_next : [];
    if (next.length) {
      nextEl.innerHTML = `
        <strong>Recommended follow-up:</strong>
        <ul>${next.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>
        <p class="muted">Autonomous thought: ${escapeHtml(data.autonomous_thought || '')}</p>
      `;
    } else {
      nextEl.textContent = '';
    }
  }
}

function updateLearningMetrics(version, delta, { track = false } = {}) {
  if (statusEls.learningVersion) {
    statusEls.learningVersion.textContent = version || '-';
  }
  if (statusEls.learningDelta) {
    statusEls.learningDelta.textContent =
      typeof delta === 'number' && !Number.isNaN(delta) ? delta.toFixed(3) : '-';
  }
  if (typeof delta === 'number' && !Number.isNaN(delta)) {
    state.lastLearningDelta = delta;
    if (track) {
      state.learningTrend.push(delta);
      if (state.learningTrend.length > 50) {
        state.learningTrend = state.learningTrend.slice(-50);
      }
      drawTrend();
    }
  }
}

function setCurrentCapsule(capsule, { updateForm = false } = {}) {
  if (!capsule) return;
  state.currentCapsule = capsule;
  if (updateForm) {
    const textEl = $('#capsule-text');
    const tagEl = $('#capsule-tag');
    if (textEl) textEl.value = capsule.text || '';
    if (tagEl) tagEl.value = capsule.tag || '';
  }
}

function getActiveCapsuleText() {
  const textEl = $('#capsule-text');
  const tagEl = $('#capsule-tag');
  const text = textEl ? textEl.value.trim() : '';
  if (text) {
    return text;
  }
  if (state.currentCapsule && state.currentCapsule.text) {
    if (textEl && !textEl.value) {
      textEl.value = state.currentCapsule.text;
    }
    if (tagEl && !tagEl.value) {
      tagEl.value = state.currentCapsule.tag || '';
    }
    return state.currentCapsule.text;
  }
  return '';
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
    statusEls.last.textContent = data.last_action || 'Awaiting activity';
    statusEls.halted.classList.toggle('hidden', !data.halted);
    const delta = typeof data.learning_delta === 'number' ? data.learning_delta : null;
    const shouldTrack =
      delta !== null && (state.learningTrend.length === 0 || state.learningTrend[state.learningTrend.length - 1] !== delta);
    updateLearningMetrics(data.learning_version, delta, { track: shouldTrack });
    if (Array.isArray(data.learning_history) && data.learning_history.length) {
      state.learningTrend = data.learning_history.map((value) => Number(value));
      drawTrend();
    }
  } catch (error) {
    console.error('Status error', error);
  }
}

async function fetchLearningHistory() {
  try {
    const data = await fetchJSON('/learning/history');
    if (Array.isArray(data.history) && data.history.length) {
      state.learningTrend = data.history.map((entry) => Number(entry.delta_score ?? 0));
      const latest = data.history[data.history.length - 1];
      updateLearningMetrics(latest.version, Number(latest.delta_score ?? 0), { track: false });
      drawTrend();
    }
  } catch (error) {
    console.warn('Learning history unavailable', error);
  }
}

async function saveCapsule() {
  const textEl = $('#capsule-text');
  const tagEl = $('#capsule-tag');
  const text = textEl ? textEl.value.trim() : '';
  const tag = tagEl && tagEl.value.trim() ? tagEl.value.trim() : 'general';
  if (!text) {
    renderJSON(statusEls.actionsOutput, 'Provide text before saving.');
    return;
  }
  try {
    const data = await fetchJSON('/capsules/save', {
      method: 'POST',
      body: JSON.stringify({ text, tag }),
    });
    renderJSON(statusEls.actionsOutput, data);
    setCurrentCapsule(data, { updateForm: false });
    await refreshCapsules();
    await refreshStatus();
    if (autoControls.analyze?.checked) {
      await analyzeCapsule({ silent: true });
    }
    if (autoControls.simulate?.checked) {
      await simulateCapsule({ silent: true });
    }
    if (autoControls.learning?.checked) {
      await runLearningStep({ silent: true });
    }
    if (autoControls.research?.checked) {
      await exploreResearch({ silent: true, topic: nextResearchTopic() });
    }
    if (autoControls.insights?.checked) {
      await fetchInsights({ silent: true });
    }
  } catch (error) {
    renderJSON(statusEls.actionsOutput, error);
  }
}

async function analyzeCapsule({ silent = false } = {}) {
  const text = getActiveCapsuleText();
  if (!text) {
    if (!silent) {
      renderJSON(statusEls.actionsOutput, 'Enter text to analyze.');
    }
    return;
  }
  try {
    const data = await fetchJSON('/capsules/analyze', {
      method: 'POST',
      body: JSON.stringify({ text }),
    });
    if (!silent) {
      renderJSON(statusEls.actionsOutput, data);
    }
    await refreshStatus();
  } catch (error) {
    renderJSON(statusEls.actionsOutput, error);
  }
}

async function simulateCapsule({ silent = false } = {}) {
  const text = getActiveCapsuleText();
  if (!text) {
    if (!silent) {
      renderJSON(statusEls.simulationOutput, 'Provide text to simulate.');
    }
    return;
  }
  try {
    const data = await fetchJSON('/simulate/run', {
      method: 'POST',
      body: JSON.stringify({ text }),
    });
    renderSimulation(data);
    await refreshStatus();
  } catch (error) {
    renderJSON(statusEls.simulationOutput, error);
  }
}

async function proposeUpgrade() {
  const text = getActiveCapsuleText();
  if (!text) {
    renderJSON(statusEls.actionsOutput, 'Enter a proposal to submit.');
    return;
  }
  try {
    const data = await fetchJSON('/upgrades/propose', {
      method: 'POST',
      body: JSON.stringify({ proposal: text, rationale: 'Submitted via dashboard' }),
    });
    renderJSON(statusEls.actionsOutput, data);
  } catch (error) {
    renderJSON(statusEls.actionsOutput, error);
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
      renderJSON(statusEls.actionsOutput, data);
    }
    await refreshStatus();
  } catch (error) {
    renderJSON(statusEls.actionsOutput, error);
  }
}

function drawTrend() {
  const canvas = statusEls.learningTrend;
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  const ratio = window.devicePixelRatio || 1;
  const width = (canvas.clientWidth || 360) * ratio;
  const height = (canvas.clientHeight || 160) * ratio;
  canvas.width = width;
  canvas.height = height;
  ctx.save();
  ctx.scale(ratio, ratio);
  ctx.clearRect(0, 0, width / ratio, height / ratio);
  const drawWidth = width / ratio;
  const drawHeight = height / ratio;
  ctx.fillStyle = 'rgba(47, 128, 237, 0.06)';
  ctx.fillRect(0, 0, drawWidth, drawHeight);

  const values = state.learningTrend;
  if (!values || values.length < 2) {
    ctx.fillStyle = '#94a3b8';
    ctx.font = '14px "Inter", sans-serif';
    ctx.fillText('Trend appears after multiple learning steps.', 12, drawHeight / 2);
    ctx.restore();
    return;
  }
  const padding = 18;
  const chartWidth = drawWidth - padding * 2;
  const chartHeight = drawHeight - padding * 2;
  const max = Math.max(...values);
  const min = Math.min(...values);
  const range = Math.max(0.01, max - min);
  const toX = (index) => padding + (index / (values.length - 1)) * chartWidth;
  const toY = (value) => padding + chartHeight - ((value - min) / range) * chartHeight;

  ctx.strokeStyle = 'rgba(148, 163, 184, 0.35)';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) {
    const y = padding + (chartHeight / 4) * i;
    ctx.beginPath();
    ctx.moveTo(padding, y);
    ctx.lineTo(padding + chartWidth, y);
    ctx.stroke();
  }

  ctx.beginPath();
  values.forEach((value, index) => {
    const x = toX(index);
    const y = toY(value);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.lineWidth = 2;
  ctx.strokeStyle = '#2f80ed';
  ctx.stroke();

  const gradient = ctx.createLinearGradient(0, padding, 0, padding + chartHeight);
  gradient.addColorStop(0, 'rgba(47, 128, 237, 0.25)');
  gradient.addColorStop(1, 'rgba(47, 128, 237, 0)');
  ctx.lineTo(padding + chartWidth, padding + chartHeight);
  ctx.lineTo(padding, padding + chartHeight);
  ctx.closePath();
  ctx.fillStyle = gradient;
  ctx.fill();

  const lastValue = values[values.length - 1];
  const lastX = toX(values.length - 1);
  const lastY = toY(lastValue);
  ctx.fillStyle = '#2f80ed';
  ctx.beginPath();
  ctx.arc(lastX, lastY, 4, 0, Math.PI * 2);
  ctx.fill();
  ctx.font = '12px "Inter", sans-serif';
  ctx.fillText(`${lastValue.toFixed(3)} Δ`, lastX - 20, Math.max(padding + 12, lastY - 10));
  ctx.restore();
}

async function refreshCapsules(params = {}) {
  const url = new URL('/capsules/list', window.location.origin);
  if (params.tag) url.searchParams.set('tag', params.tag);
  if (params.query) url.searchParams.set('q', params.query);
  try {
    const data = await fetchJSON(url.toString(), { method: 'GET' });
    statusEls.capsuleList.innerHTML = '';
    const capsules = Array.isArray(data.capsules) ? data.capsules : [];
    if (!capsules.length) {
      const li = document.createElement('li');
      li.className = 'empty';
      li.textContent = 'No capsules saved yet.';
      statusEls.capsuleList.appendChild(li);
      return;
    }
    capsules.forEach((capsule, index) => {
      const li = document.createElement('li');
      li.innerHTML = `
        <span class="tag">${escapeHtml(capsule.tag || 'untagged')}</span>
        <div>${escapeHtml(capsule.text || '')}</div>
        <small>${escapeHtml(capsule.created_at || '')}</small>
      `;
      li.addEventListener('click', () => {
        setCurrentCapsule(capsule, { updateForm: true });
      });
      statusEls.capsuleList.appendChild(li);
      if (index === 0 && !state.currentCapsule) {
        setCurrentCapsule(capsule);
      }
    });
  } catch (error) {
    statusEls.capsuleList.innerHTML = `<li class="error">${escapeHtml(JSON.stringify(error))}</li>`;
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

function nextResearchTopic() {
  if (state.researchTopics.length === 0) {
    return '';
  }
  const topic = state.researchTopics[state.researchIndex % state.researchTopics.length];
  state.researchIndex = (state.researchIndex + 1) % state.researchTopics.length;
  return topic;
}

async function exploreResearch({ silent = false, topic } = {}) {
  const input = $('#research-query');
  const query = topic || (input ? input.value.trim() : '');
  if (topic && input) {
    input.value = topic;
  }
  if (!query) {
    if (!silent) {
      renderJSON(statusEls.researchOutput, 'Enter a topic to explore.');
    }
    return;
  }
  try {
    const data = await fetchJSON(`/research/explore?q=${encodeURIComponent(query)}`);
    renderResearch(data);
    await refreshStatus();
  } catch (error) {
    renderJSON(statusEls.researchOutput, error);
  }
}

async function fetchInsights({ silent = false } = {}) {
  try {
    const data = await fetchJSON('/insights/reflect');
    renderInsights(data);
    if (!silent) {
      await refreshStatus();
    }
  } catch (error) {
    renderJSON(statusEls.insightsOutput, error);
  }
}

function setAutoAction(key, enabled, intervalMs, handler, optionsFactory) {
  if (state.autoIntervals[key]) {
    clearInterval(state.autoIntervals[key]);
    state.autoIntervals[key] = null;
  }
  try {
    localStorage.setItem(`gaia-auto-${key}`, enabled ? 'true' : 'false');
  } catch (err) {
    /* ignore storage errors */
  }
  if (enabled) {
    const run = () => {
      const extra = (typeof optionsFactory === 'function' ? optionsFactory() : {}) || {};
      handler({ silent: true, ...extra });
    };
    run();
    state.autoIntervals[key] = setInterval(run, intervalMs);
  }
}

function restoreAutoToggle(control, key, intervalMs, handler, optionsFactory) {
  if (!control) return;
  control.addEventListener('change', () => setAutoAction(key, control.checked, intervalMs, handler, optionsFactory));
  try {
    const saved = localStorage.getItem(`gaia-auto-${key}`);
    if (saved === 'true') {
      control.checked = true;
      setAutoAction(key, true, intervalMs, handler, optionsFactory);
    }
  } catch (err) {
    /* ignore */
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
  if (!toggle) return;
  const saved = localStorage.getItem('gaia-dark-mode');
  if (saved === 'true') {
    document.body.classList.add('dark-mode');
    toggle.checked = true;
  }
  toggle.addEventListener('change', () => {
    document.body.classList.toggle('dark-mode', toggle.checked);
    localStorage.setItem('gaia-dark-mode', toggle.checked ? 'true' : 'false');
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

  restoreAutoToggle(autoControls.analyze, 'analyze', 20000, analyzeCapsule);
  restoreAutoToggle(autoControls.simulate, 'simulate', 30000, simulateCapsule);
  restoreAutoToggle(autoControls.learning, 'learning', 45000, runLearningStep);
  restoreAutoToggle(autoControls.research, 'research', 60000, exploreResearch, () => ({ topic: nextResearchTopic() }));
  restoreAutoToggle(autoControls.insights, 'insights', 90000, fetchInsights);
}

async function boot() {
  initTabs();
  initDarkMode();
  initEvents();
  await refreshStatus();
  await fetchLearningHistory();
  await refreshCapsules();
  await refreshLogs();
  await fetchInsights({ silent: true });
  drawTrend();
  setInterval(refreshStatus, 7000);
  setInterval(refreshLogs, 10000);
  setInterval(() => refreshCapsules(), 15000);
}

document.addEventListener('DOMContentLoaded', boot);
