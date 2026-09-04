(function () {
  'use strict';
  const root = document.querySelector('[data-page="daily-report"]');
  if (!root) return;
  const $ = (selector) => root.querySelector(selector);
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const message = (error) => window.ResearchApp.errorMessage(error.status, error.payload);
  const request = async (url) => window.ResearchApp.readJson(await window.ResearchApp.apiFetch(url));
  const state = (text, type, requestId) => window.ResearchApp.renderState($('#daily-report-state'), type || '', text, requestId);
  const list = (value) => Array.isArray(value) ? value : [];

  function renderRows(selector, rows, columns, emptyMessage) {
    const body = $(selector).querySelector('tbody');
    body.replaceChildren();
    rows.forEach((item) => {
      const row = document.createElement('tr');
      row.innerHTML = columns.map((column) => `<td>${esc(column(item))}</td>`).join('');
      body.append(row);
    });
    if (!rows.length) {
      const row = document.createElement('tr');
      row.innerHTML = `<td colspan="${columns.length}" class="muted">${esc(emptyMessage)}</td>`;
      body.append(row);
    }
  }

  function render(report) {
    const summary = [
      ['报告日期', report.report_date], ['运行号', report.run_id || '未生成'], ['状态', report.status],
      ['结果可用', report.result_usable ? '是' : '否'], ['风险状态', report.risk_state || report.risk?.state || '未提供'],
    ];
    $('#daily-report-summary').innerHTML = summary.map(([name, value]) => `<dt>${esc(name)}</dt><dd>${esc(value ?? '未提供')}</dd>`).join('');
    const quality = [
      ['数据质量', report.data_quality], ['市场开关', report.market_switch == null ? '未提供' : report.market_switch ? '开启' : '关闭'],
      ['实际成交录入', report.actual_execution_input == null ? '未提供' : report.actual_execution_input ? '已录入' : '未录入'],
      ['提示', report.notice || '服务端未提供提示'],
    ];
    $('#daily-report-quality').innerHTML = quality.map(([name, value]) => `<dt>${esc(name)}</dt><dd>${esc(value ?? '未提供')}</dd>`).join('');
    const unavailable = list(report.unavailable_reasons);
    $('#daily-report-unavailable').hidden = !unavailable.length;
    $('#daily-report-unavailable').textContent = unavailable.join('；');
    const account = report.account || report.account_snapshot || {};
    const accountFields = ['account_id', 'cash', 'equity', 'market_value', 'drawdown', 'risk_state'];
    $('#daily-report-account').textContent = Object.keys(account).length
      ? accountFields.filter((key) => account[key] != null).map((key) => `${key}: ${account[key]}`).join('；')
      : '服务端未提供账户快照。';
    renderRows('#daily-report-holdings', list(report.holdings), [
      (item) => item.symbol || item.security || item.security_id, (item) => item.quantity,
      (item) => item.available_quantity, (item) => item.avg_cost || item.cost, (item) => item.market_value || item.value,
    ], '暂无持仓。');
    renderRows('#daily-report-candidates', list(report.candidates), [
      (item) => item.symbol || item.security, (item) => item.condition || item.trigger_reasons || item.reason,
      (item) => item.score, (item) => item.risk_tags || item.risk_label || item.risk_state,
    ], '暂无候选。');
    renderRows('#daily-report-plans', list(report.order_plans || report.plans), [
      (item) => item.plan_no || item.plan_id, (item) => item.side, (item) => item.quantity,
      (item) => item.status, (item) => item.execution_date || item.expires_at,
    ], '暂无计划。');
  }

  async function load() {
    state('正在加载日报…', 'loading');
    try {
      const payload = await request(root.dataset.apiUrl);
      render(payload.data || {});
      state('日报已加载。', 'success', payload.request_id);
    } catch (error) {
      state(message(error), 'error', error.payload?.request_id);
    }
  }

  $('#daily-report-refresh').addEventListener('click', load);
  load();
}());
