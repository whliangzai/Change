(function () {
  'use strict';
  const root = document.querySelector('[data-page="order-plans"]');
  if (!root) return;
  const $ = (selector) => root.querySelector(selector);
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const message = (error) => window.ResearchApp.errorMessage(error.status, error.payload);
  const request = async (url, options) => window.ResearchApp.readJson(await window.ResearchApp.apiFetch(url, options));
  const state = (text, type, requestId) => window.ResearchApp.renderState($('#plan-state'), type || '', text, requestId);
  let currentPage = 1;
  let activeKey = null;

  function renderPager(data) {
    const target = $('#plan-pagination'); target.replaceChildren();
    const pages = Math.max(1, Math.ceil((data.total || 0) / (data.page_size || 50)));
    const label = document.createElement('span'); label.textContent = `第 ${data.page || 1}/${pages} 页，共 ${data.total || 0} 条`;
    target.append(label);
    [['上一页', (data.page || 1) - 1], ['下一页', (data.page || 1) + 1]].forEach(([text, page]) => {
      const button = document.createElement('button'); button.type = 'button'; button.textContent = text;
      button.disabled = page < 1 || page > pages; button.addEventListener('click', () => load(page)); target.append(button);
    });
  }

  function render(data) {
    const body = $('#plan-table tbody'); body.replaceChildren();
    (data.items || []).forEach((plan) => {
      const row = document.createElement('tr');
      const risk = plan.risk_snapshot?.risk_state || plan.risk_snapshot?.state || '未提供';
      row.innerHTML = `<td><code>${esc(plan.plan_no || plan.plan_id)}</code></td><td>${esc(plan.side)}</td><td>${esc(plan.security_id || plan.symbol)}</td><td>${esc(plan.quantity)}</td><td>${esc(plan.reference_low)} - ${esc(plan.reference_high)}</td><td>${esc(plan.estimated_cost)}</td><td>${esc(risk)}</td><td>${esc(plan.status)}</td><td>${esc(plan.version)}</td><td></td>`;
      const action = document.createElement('button'); action.type = 'button'; action.textContent = '人工审核';
      action.disabled = plan.status !== 'PENDING_CONFIRMATION';
      action.addEventListener('click', () => selectPlan(plan)); row.lastElementChild.append(action); body.append(row);
    });
    if (!(data.items || []).length) body.innerHTML = '<tr><td colspan="10" class="muted">该日期没有可见计划。</td></tr>';
    renderPager(data);
  }

  function selectPlan(plan) {
    const form = $('#plan-decision-form');
    form.plan_id.value = plan.plan_id; form.expected_version.value = String(plan.version);
    form.review_note.value = ''; activeKey = null;
    $('#plan-decision-target').textContent = `${plan.plan_no || plan.plan_id}，当前状态 ${plan.status}，版本 ${plan.version}`;
    $('#plan-decision-panel').hidden = false; form.review_note.focus();
  }

  async function load(page) {
    currentPage = page || 1;
    const date = $('#plan-execution-date').value;
    if (!date) return state('请选择执行日期。', 'error');
    state('正在加载计划…', 'loading');
    try {
      const payload = await request(`${root.dataset.apiUrl}?execution_date=${encodeURIComponent(date)}&page=${currentPage}&page_size=50`);
      render(payload.data || {}); state('计划已加载。', 'success', payload.request_id);
    } catch (error) { state(message(error), 'error', error.payload?.request_id); }
  }

  $('#plan-filter').addEventListener('submit', (event) => { event.preventDefault(); load(1); });
  $('#plan-decision-cancel').addEventListener('click', () => { $('#plan-decision-panel').hidden = true; });
  $('#plan-decision-form').addEventListener('submit', async (event) => {
    event.preventDefault(); const form = event.currentTarget; const values = Object.fromEntries(new FormData(form).entries());
    if (!values.review_note.trim()) return state('提交人工决定前必须填写审核备注。', 'error');
    const submit = $('#plan-decision-submit'); submit.disabled = true;
    activeKey = activeKey || window.ResearchApp.idempotency(); state('正在提交人工决定…', 'loading');
    try {
      const payload = await request(`${root.dataset.apiUrl}/${encodeURIComponent(values.plan_id)}/confirm`, { method: 'POST', headers: { 'Idempotency-Key': activeKey }, body: { decision: values.decision, review_note: values.review_note.trim(), expected_version: Number(values.expected_version) } });
      state(`服务端已记录：${payload.data?.status || '已完成'}。`, 'success', payload.request_id); $('#plan-decision-panel').hidden = true; activeKey = null; await load(currentPage);
    } catch (error) { state(message(error), 'error', error.payload?.request_id); submit.disabled = false; }
  });
  load(1);
}());
