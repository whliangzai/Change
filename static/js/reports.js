(function () {
  'use strict';
  const root = document.querySelector('[data-page="reports"]');
  if (!root) return;
  const $ = (selector) => root.querySelector(selector);
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const message = (error) => window.ResearchApp.errorMessage(error.status, error.payload);
  const request = async (url) => window.ResearchApp.readJson(await window.ResearchApp.apiFetch(url));
  const state = (text, type, requestId) => window.ResearchApp.renderState($('#reports-state'), type || '', text, requestId);

  function showPairs(target, pairs) { $(target).innerHTML = pairs.map(([key, value]) => `<dt>${esc(key)}</dt><dd>${esc(value ?? '未提供')}</dd>`).join(''); }
  async function loadRuns() {
    state('正在加载回测运行…', 'loading');
    try {
      const payload = await request(`${root.dataset.apiUrl}?page=1&page_size=200`); const runs = payload.data?.items || []; const select = $('#report-run-select'); select.replaceChildren(); select.append(new Option(runs.length ? '请选择回测运行' : '暂无可查看运行', ''));
      runs.forEach((run) => select.append(new Option(`${run.run_id} | ${run.status} | ${run.result_usable ? '结果可用' : '结果不可用'}`, run.run_id)));
      state(runs.length ? `已加载 ${runs.length} 条运行。` : '暂无可查看的回测运行。', runs.length ? 'success' : '', payload.request_id);
    } catch (error) { state(message(error), 'error', error.payload?.request_id); }
  }
  async function loadReport() {
    const runId = $('#report-run-select').value; if (!runId) return state('请选择回测运行。', 'error');
    state('正在加载服务端报告…', 'loading');
    try {
      const payload = await request(`/api/v1/backtests/${encodeURIComponent(runId)}/report`); const report = payload.data || {};
      showPairs('#report-summary', [['运行号', report.run_id || runId], ['状态', report.status], ['结果可用', report.result_usable ? '是' : '否'], ['快照哈希', report.snapshot_hash]]);
      showPairs('#report-metrics', Object.entries(report.metrics || {}).length ? Object.entries(report.metrics || {}) : [['指标', '服务端尚未提供可用指标']]);
      const unavailable = report.unavailable_reasons || []; $('#report-unavailable').hidden = !unavailable.length; $('#report-unavailable').textContent = unavailable.join('；');
      const deviation = report.plan_actual_deviation ?? report.deviations ?? report.cost ?? null; $('#report-deviation').textContent = deviation == null ? '服务端报告未提供计划/实际偏差或成本明细。' : JSON.stringify(deviation, null, 2);
      state(report.result_usable ? '报告已加载。' : '报告已加载，但服务端标记为不可用。', report.result_usable ? 'success' : 'error', payload.request_id);
    } catch (error) { state(message(error), 'error', error.payload?.request_id); }
  }
  $('#reports-refresh').addEventListener('click', loadRuns); $('#report-load').addEventListener('click', loadReport); $('#report-run-select').addEventListener('change', loadReport); loadRuns();
}());
