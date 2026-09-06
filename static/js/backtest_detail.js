(function () {
  'use strict';
  const root = document.querySelector('[data-page="backtest-detail"]');
  if (!root) return;
  const $ = (selector) => root.querySelector(selector);
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const request = async (url, options) => window.ResearchApp.readJson(await window.ResearchApp.apiFetch(url, options));
  const state = (target, text, type, requestId) => window.ResearchApp.renderState($(target), type || '', text, requestId);
  const message = (error) => window.ResearchApp.errorMessage(error.status, error.payload);
  let isSubmitting = false;
  let executeKey = null;

  function requestId(error) { return error.payload?.request_id; }

  function renderFailure(target, error, retry) {
    const type = error.status === 403 ? 'permission' : (error.status === 503 ? 'unavailable' : 'error');
    state(target, message(error), type, requestId(error));
    if (retry) {
      const button = document.createElement('button');
      button.type = 'button'; button.textContent = '重试'; button.addEventListener('click', retry);
      $(target).append(document.createTextNode(' '), button);
    }
  }

  function unavailable(target, reasons, fallback) {
    const values = Array.isArray(reasons) ? reasons.filter(Boolean) : [reasons].filter(Boolean);
    const element = $(target);
    element.hidden = false;
    element.className = 'state unavailable';
    element.textContent = values.length ? values.join('；') : fallback;
  }

  function clearUnavailable(target) { $(target).hidden = true; }

  function setExecute(run) {
    const button = $('#backtest-execute');
    const runnable = ['QUEUED', 'FAILED', 'READY'].includes(run.status);
    button.hidden = !runnable;
    button.disabled = isSubmitting;
    button.textContent = isSubmitting ? '正在请求执行…' : button.dataset.submitLabel;
  }

  function renderRun(run) {
    const fields = [
      ['运行号', run.run_id], ['运行状态', run.status], ['数据批次', run.data_batch_id],
      ['策略版本', run.strategy_version_id], ['成本版本', run.cost_config_id], ['规则版本', run.rule_config_id],
      ['日期区间', `${run.start_date || '--'} 至 ${run.end_date || '--'}`],
      ['结果可用性', run.result_usable ? '可用' : '尚不可用'], ['结果快照', run.snapshot_hash || '--'],
      ['基准', run.benchmark_symbol || '--'], ['初始权益', run.initial_equity || '--'],
    ];
    $('#run-summary').innerHTML = fields.map(([label, value]) => `<dt>${esc(label)}</dt><dd><code>${esc(value ?? '--')}</code></dd>`).join('');
    const stages = Array.isArray(run.stages) ? run.stages : [];
    $('#run-stages').innerHTML = stages.length
      ? `<h3>阶段进度</h3><div class="table-wrap"><table><thead><tr><th>阶段</th><th>状态</th><th>进度</th><th>说明</th></tr></thead><tbody>${stages.map((stage) => `<tr><td>${esc(stage.name || stage.stage || '--')}</td><td>${esc(stage.status || '--')}</td><td class="numeric">${stage.progress == null ? '--' : `${esc(stage.progress)}%`}</td><td>${esc(stage.error || '--')}</td></tr>`).join('')}</tbody></table></div>`
      : '<p class="muted">服务端尚未提供阶段进度；请以运行状态和结果可用性为准。</p>';
    const reasons = run.unavailable_reasons || run.reason;
    if (run.result_usable) clearUnavailable('#run-unavailable');
    else unavailable('#run-unavailable', reasons, '运行结果尚不可用。请等待运行完成，或检查运行阶段中的失败原因。');
    state('#backtest-preconditions', run.result_usable ? '数据批次、策略版本与结果均可用于研究复核。' : '运行记录已加载；结果尚不可用，数据批次与版本仅作为本次研究运行的证据。', run.result_usable ? 'success' : 'unavailable');
    setExecute(run);
  }

  async function loadChild(id, kind) {
    const stateTarget = `#${kind}-state`;
    const unavailableTarget = `#${kind}-unavailable`;
    state(stateTarget, kind === 'report' ? '正在加载绩效报告…' : '正在加载模拟交易明细…', 'loading');
    clearUnavailable(unavailableTarget);
    try {
      const payload = await request(`/api/v1/backtests/${encodeURIComponent(id)}/${kind}`);
      const data = payload.data || {};
      if (kind === 'report') {
        const metrics = data.metrics || {};
        $('#report-metrics').innerHTML = Object.entries(metrics).map(([key, value]) => `<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`).join('') || '<dt>指标</dt><dd>--</dd>';
        if (data.result_usable) state(stateTarget, '绩效报告可用于研究复核。', 'success', payload.request_id);
        else {
          state(stateTarget, '绩效报告尚不可用。', 'unavailable', payload.request_id);
          unavailable(unavailableTarget, data.unavailable_reasons, '请等待运行完成后再查看指标和报告快照。');
        }
      } else {
        const body = $('#trades-table tbody'); body.replaceChildren();
        (data.items || []).forEach((trade) => {
          const row = document.createElement('tr');
          row.innerHTML = `<td class="date-value">${esc(trade.trade_date || trade.date || '--')}</td><td><code>${esc(trade.symbol || '--')}</code></td><td>${esc(trade.side || '--')}</td><td class="numeric">${esc(trade.quantity ?? '--')}</td><td class="numeric">${esc(trade.price || trade.execution_price || '--')}</td><td class="numeric">${esc(trade.fees || trade.cost || '--')}</td>`;
          body.append(row);
        });
        if (data.items?.length) state(stateTarget, `已加载 ${data.items.length} 笔模拟交易明细。`, 'success', payload.request_id);
        else if (data.result_usable === false || data.status && data.status !== 'SUCCEEDED') {
          state(stateTarget, '模拟交易明细尚不可用。', 'unavailable', payload.request_id);
          unavailable(unavailableTarget, data.unavailable_reasons, '运行完成并产生结果后，才会展示模拟交易明细。');
        } else state(stateTarget, '本次回测没有模拟交易明细。', '', payload.request_id);
      }
    } catch (error) { renderFailure(stateTarget, error, () => loadChild(id, kind)); }
  }

  async function loadDetail(id) {
    state('#backtest-state', '正在加载运行详情…', 'loading');
    $('#backtest-detail-panel').hidden = false;
    try {
      const payload = await request(`/api/v1/backtests/${encodeURIComponent(id)}`);
      renderRun(payload.data || {});
      state('#backtest-state', '运行详情已加载。', 'success', payload.request_id);
      await Promise.all([loadChild(id, 'report'), loadChild(id, 'trades')]);
    } catch (error) { renderFailure('#backtest-state', error, () => loadDetail(id)); }
  }

  async function loadList() {
    state('#backtest-state', '正在加载回测列表…', 'loading');
    try {
      const payload = await request('/api/v1/backtests?page=1&page_size=50');
      const data = payload.data || {}; const body = $('#backtest-list tbody'); body.replaceChildren();
      (data.items || []).forEach((run) => {
        const row = document.createElement('tr');
        row.innerHTML = `<td><code>${esc(run.run_id)}</code></td><td>${esc(run.status || '--')}</td><td class="date-value">${esc(run.start_date || '--')} 至 ${esc(run.end_date || '--')}</td><td>${run.result_usable ? '可用' : '尚不可用'}</td><td></td>`;
        const button = document.createElement('button'); button.type = 'button'; button.textContent = '查看运行';
        button.addEventListener('click', () => { location.href = `/backtests/${encodeURIComponent(run.run_id)}/view`; });
        row.lastElementChild.append(button); body.append(row);
      });
      state('#backtest-state', data.items?.length ? `已加载 ${data.items.length} 条运行。` : '暂无回测运行。请先创建满足前置条件的研究回测。', data.items?.length ? 'success' : 'unavailable', payload.request_id);
    } catch (error) { renderFailure('#backtest-state', error, loadList); }
  }

  async function execute() {
    const id = root.dataset.runId;
    if (!id || isSubmitting) return;
    isSubmitting = true; executeKey = executeKey || window.ResearchApp.idempotency(); setExecute({ status: 'READY' });
    state('#backtest-state', '正在请求服务端执行回测；结果是否可用以服务端运行状态为准。', 'loading');
    try {
      const payload = await request(`/api/v1/backtests/${encodeURIComponent(id)}/execute`, { method: 'POST', headers: { 'Idempotency-Key': executeKey } });
      state('#backtest-state', `服务端已受理回测执行：${payload.data?.status || '状态待刷新'}。`, 'success', payload.request_id);
      executeKey = null; isSubmitting = false; await loadDetail(id);
    } catch (error) {
      renderFailure('#backtest-state', error, execute);
      isSubmitting = false; setExecute({ status: 'READY' });
    }
  }

  $('#backtest-refresh').addEventListener('click', () => root.dataset.runId ? loadDetail(root.dataset.runId) : loadList());
  $('#backtest-execute').addEventListener('click', execute);
  root.dataset.runId ? loadDetail(root.dataset.runId) : loadList();
}());
