(function () {
  'use strict';
  const root = document.querySelector('[data-page="backtest-detail"]');
  if (!root) return;
  const $ = (selector) => root.querySelector(selector);
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const request = async (url, options) => window.ResearchApp.readJson(await window.ResearchApp.apiFetch(url, options));
  const state = (target, text, type, requestId) => window.ResearchApp.renderState($(target), type || '', text, requestId);
  const message = (error) => window.ResearchApp.errorMessage(error.status, error.payload);
  const statusLabel = (value, domain) => window.ResearchApp.statusLabel ? window.ResearchApp.statusLabel(value, domain) : String(value ?? '--');
  let isSubmitting = false;
  let executeKey = null;
  let executeConfirming = false;
  let listPage = 1;
  let listRequest = 0;
  let currentRun = null;

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
    button.textContent = isSubmitting ? '正在请求执行…' : (executeConfirming ? '返回修改' : button.dataset.submitLabel);
  }

  function showExecuteConfirmation(run) {
    const target = $('#backtest-execute-summary'); target.replaceChildren();
    [['对象', run.run_id || root.dataset.runId], ['状态', window.ResearchApp.statusLabel ? window.ResearchApp.statusLabel(run.status, 'job') : (run.status || '--')], ['数据批次 / 策略版本', `${run.data_batch_id || '--'} / ${run.strategy_version_id || '--'}`], ['日期区间', `${run.start_date || '--'} 至 ${run.end_date || '--'}`], ['关键影响', '服务端将执行既有研究运行并写入不可变结果证据；不会产生委托或自动交易。']].forEach(([label, value]) => { const term = document.createElement('dt'); term.textContent = label; const definition = document.createElement('dd'); definition.textContent = String(value ?? '--'); target.append(term, definition); });
    executeConfirming = true; $('#backtest-execute-confirmation').hidden = false; $('#backtest-execute').textContent = '返回修改'; $('#backtest-execute-confirm').focus();
  }

  function renderRun(run) {
    currentRun = run;
    const fields = [
      ['运行号', run.run_id], ['运行状态', statusLabel(run.status, 'job')], ['数据批次', run.data_batch_id],
      ['策略版本', run.strategy_version_id], ['成本版本', run.cost_config_id], ['规则版本', run.rule_config_id],
      ['日期区间', `${run.start_date || '--'} 至 ${run.end_date || '--'}`],
      ['策略类型', run.strategy_type || '--'], ['引擎版本', run.engine_version || '--'],
      ['策略实现', run.strategy_implementation_version || '--'], ['有效参数', JSON.stringify(run.effective_parameters || {})],
      ['结果可用性', run.result_usable ? '可用' : '尚不可用'], ['结果快照', run.snapshot_hash || '--'],
      ['基准', run.benchmark_symbol || '--'], ['初始权益', run.initial_equity || '--'],
    ];
    $('#run-summary').innerHTML = fields.map(([label, value]) => `<dt>${esc(label)}</dt><dd><code>${esc(value ?? '--')}</code></dd>`).join('');
    const stages = Array.isArray(run.stages) ? run.stages : [];
    $('#run-stages').innerHTML = stages.length
      ? `<h3>阶段进度</h3><div class="table-wrap"><table><caption>回测执行阶段与服务端状态</caption><thead><tr><th scope="col">阶段</th><th scope="col">状态</th><th scope="col">进度</th><th scope="col">说明</th></tr></thead><tbody>${stages.map((stage) => `<tr><td>${esc(stage.name || stage.stage || '--')}</td><td><span class="status">${esc(stage.status || '--')}</span></td><td class="numeric">${stage.progress == null ? '--' : `${esc(stage.progress)}%`}</td><td>${esc(stage.error || '--')}</td></tr>`).join('')}</tbody></table></div>`
      : '<p class="muted">服务端尚未提供阶段进度；请以运行状态和结果可用性为准。</p>';
    const reasons = run.unavailable_reasons || run.reason;
    if (run.result_usable) clearUnavailable('#run-unavailable');
    else unavailable('#run-unavailable', reasons, '运行结果尚不可用。请等待运行完成，或检查运行阶段中的失败原因。');
    state('#backtest-preconditions', run.result_usable ? '数据批次、策略版本与结果均可用于研究复核。' : '运行记录已加载；结果尚不可用，数据批次与版本仅作为本次研究运行的证据。', run.result_usable ? 'success' : 'unavailable');
    setExecute(run);
  }

  function renderSeries(series) {
    const entries = Object.entries(series || {}).filter(([, item]) => item?.points?.length);
    const figure = $('#equity-series'); const svg = $('#equity-chart'); const legend = $('#equity-legend');
    svg.replaceChildren(); legend.replaceChildren(); figure.hidden = !entries.length;
    if (!entries.length) return;
    const colors = { STRATEGY: '#185adb', BENCHMARK_PRIMARY: '#16704a', UNIVERSE_EQUAL_WEIGHT: '#b56b00', BENCHMARK_SECONDARY: '#7553a6' };
    const labels = { STRATEGY: '策略', BENCHMARK_PRIMARY: '主基准', UNIVERSE_EQUAL_WEIGHT: '股票池等权（合成、不可投资）', BENCHMARK_SECONDARY: '次基准' };
    const values = entries.flatMap(([, item]) => item.points.map((point) => Number(point.value))).filter(Number.isFinite);
    const min = Math.min(...values); const max = Math.max(...values); const span = max - min || 1;
    [0, 0.5, 1].forEach((ratio) => {
      const y = 24 + ratio * 260; const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('x1', '64'); line.setAttribute('x2', '940'); line.setAttribute('y1', y); line.setAttribute('y2', y); line.setAttribute('class', 'chart-grid'); svg.append(line);
      const text = document.createElementNS('http://www.w3.org/2000/svg', 'text'); text.setAttribute('x', '8'); text.setAttribute('y', y + 4); text.setAttribute('class', 'chart-label'); text.textContent = (max - ratio * span).toFixed(2); svg.append(text);
    });
    entries.forEach(([code, item]) => {
      const points = item.points.map((point, index) => {
        const x = 64 + (item.points.length === 1 ? 0 : index / (item.points.length - 1)) * 876;
        const y = 24 + (max - Number(point.value)) / span * 260;
        return `${x.toFixed(2)},${y.toFixed(2)}`;
      }).join(' ');
      const polyline = document.createElementNS('http://www.w3.org/2000/svg', 'polyline'); polyline.setAttribute('points', points); polyline.setAttribute('fill', 'none'); polyline.setAttribute('stroke', colors[code] || '#5d6b78'); polyline.setAttribute('class', 'equity-line'); svg.append(polyline);
      const key = document.createElement('span'); key.style.setProperty('--series-color', colors[code] || '#5d6b78'); key.textContent = `${labels[code] || code}${item.availability === 'SYNTHETIC_NOT_INVESTABLE' ? ' · 合成' : ''}`; legend.append(key);
    });
  }

  function renderSegmentMetrics(segmentMetrics) {
    const body = $('#segment-metrics tbody'); body.replaceChildren();
    Object.entries(segmentMetrics || {}).sort().forEach(([key, metrics]) => {
      const row = document.createElement('tr');
      row.innerHTML = `<th scope="row">${esc(key)}</th><td class="numeric" data-label="收益">${esc(metrics.total_return ?? '--')}</td><td class="numeric" data-label="最大回撤">${esc(metrics.max_drawdown ?? '--')}</td><td class="numeric" data-label="夏普">${esc(metrics.sharpe ?? '--')}</td>`;
      body.append(row);
    });
    if (!body.children.length) { const row = document.createElement('tr'); row.innerHTML = '<td colspan="4">尚无可用分段指标</td>'; body.append(row); }
  }

  function renderMetricValue(value) {
    if (value === null || value === undefined || value === '') return document.createTextNode('--');
    if (typeof value !== 'object') return document.createTextNode(String(value));
    const entries = Array.isArray(value) ? value.map((item, index) => [String(index + 1), item]) : Object.entries(value);
    if (!entries.length) return document.createTextNode('暂无数据');
    const list = document.createElement('dl'); list.className = 'metric-breakdown';
    entries.forEach(([key, item]) => {
      const term = document.createElement('dt'); term.textContent = key;
      const definition = document.createElement('dd');
      definition.textContent = typeof item === 'object' && item !== null ? JSON.stringify(item) : String(item ?? '--');
      list.append(term, definition);
    });
    return list;
  }

  function renderFullMetrics(metrics) {
    const labels = {
      available: '指标可用', total_return: '区间收益', max_drawdown: '最大回撤', sharpe: '夏普比率',
      win_rate: '胜率', profit_loss_ratio: '盈亏比', average_holding_period: '平均持有交易日',
      turnover: '换手率', total_cost: '总成本', industry_exposure: '行业暴露',
      monthly_returns: '月度收益', yearly_returns: '年度收益', reason: '不可用原因',
    };
    const target = $('#report-metrics'); target.replaceChildren();
    Object.entries(metrics || {}).forEach(([key, value]) => {
      const term = document.createElement('dt'); term.textContent = labels[key] || key;
      const definition = document.createElement('dd'); definition.append(renderMetricValue(value));
      target.append(term, definition);
    });
    if (!target.children.length) {
      const term = document.createElement('dt'); term.textContent = '指标';
      const definition = document.createElement('dd'); definition.textContent = '--';
      target.append(term, definition);
    }
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
        renderFullMetrics(metrics);
        renderSeries(data.series);
        renderSegmentMetrics(data.segment_metrics);
        if (data.result_usable) state(stateTarget, '绩效报告可用于研究复核。', 'success', payload.request_id);
        else {
          state(stateTarget, '绩效报告尚不可用。', 'unavailable', payload.request_id);
          unavailable(unavailableTarget, data.unavailable_reasons, '请等待运行完成后再查看指标和报告快照。');
        }
      } else {
        const body = $('#trades-table tbody'); body.replaceChildren();
        (data.items || []).forEach((trade) => {
          const row = document.createElement('tr');
          row.innerHTML = `<td class="date-value">${esc(trade.execution_date || trade.trade_date || trade.date || '--')}</td><td><code>${esc(trade.symbol || '--')}</code></td><td>${esc(trade.side || '--')}</td><td>${esc(trade.status || '--')}</td><td class="numeric">${esc(trade.quantity ?? '--')}</td><td class="numeric">${esc(trade.price || trade.execution_price || '--')}</td><td class="numeric">${esc(trade.fees || trade.cost || '--')}</td><td>${esc(trade.reason || (trade.trigger_reasons || []).join(', ') || '--')}</td>`;
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

  async function loadList(page = listPage) {
    const requestNumber = ++listRequest;
    const pager = $('#backtest-pagination');
    pager.replaceChildren();
    $('#backtest-empty-banner').hidden = true;
    state('#backtest-state', '正在加载回测列表…', 'loading');
    try {
      const payload = await request(`/api/v1/backtests?page=${page}&page_size=50`);
      if (requestNumber !== listRequest) return;
      const data = payload.data || {}; const body = $('#backtest-list tbody'); body.replaceChildren();
      const total = Number(data.total || 0);
      const pages = Math.max(1, Math.ceil(total / 50));
      if (page > pages) return loadList(pages);
      listPage = page;
      $('#backtest-count').textContent = `共 ${total} 条`;
      $('#backtest-table-wrap').hidden = false;
      $('#backtest-empty-banner').hidden = total !== 0;
      $('#backtest-empty').hidden = total !== 0;
      if (!(data.items || []).length) {
        const row = document.createElement('tr');
        row.className = 'empty-table-row';
        row.innerHTML = '<td colspan="6"><div class="table-empty"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M6 4h9l3 3v13H6z"/><path d="M9 10h6M9 14h6"/><circle cx="18" cy="18" r="3.5"/><path d="M18 16.5v3M16.5 18h3"/></svg><strong>暂无回测运行数据</strong><span>点击右上角“新建回测”开始创建</span></div></td>';
        body.append(row);
      }
      (data.items || []).forEach((run) => {
        const row = document.createElement('tr');
        row.innerHTML = `<td><code>${esc(run.run_id)}</code></td><td><code>${esc(run.strategy_version_id || '--')}</code></td><td>${esc(statusLabel(run.status, 'job'))}</td><td class="date-value">${esc(run.start_date || '--')} 至 ${esc(run.end_date || '--')}</td><td>${run.result_usable ? '可用' : '尚不可用'}</td><td></td>`;
        const button = document.createElement('button'); button.type = 'button'; button.textContent = '查看运行';
        button.addEventListener('click', () => { location.href = `/backtests/${encodeURIComponent(run.run_id)}/view`; });
        row.lastElementChild.append(button); body.append(row);
      });
      $('#backtest-state').hidden = true;
      const summary = document.createElement('span');
      const start = total ? ((page - 1) * 50) + 1 : 0;
      const end = total ? Math.min(page * 50, total) : 0;
      summary.textContent = `${start} – ${end} / ${total}`;
      pager.append(summary);
      const prev = document.createElement('button');
      prev.type = 'button'; prev.className = 'pagination-button pagination-prev'; prev.setAttribute('aria-label', '上一页'); prev.textContent = '‹';
      prev.disabled = page <= 1; prev.addEventListener('click', () => loadList(page - 1)); pager.append(prev);
      const current = document.createElement('span'); current.className = 'pagination-current'; current.textContent = String(page); pager.append(current);
      const next = document.createElement('button');
      next.type = 'button'; next.className = 'pagination-button pagination-next'; next.setAttribute('aria-label', '下一页'); next.textContent = '›';
      next.disabled = page >= pages; next.addEventListener('click', () => loadList(page + 1)); pager.append(next);
    } catch (error) {
      if (requestNumber !== listRequest) return;
      $('#backtest-table-wrap').hidden = false;
      $('#backtest-empty-banner').hidden = true;
      $('#backtest-count').textContent = '加载失败';
      renderFailure('#backtest-state', error, () => loadList(page));
    }
  }

  async function submitExecution() {
    const id = root.dataset.runId;
    if (!id || isSubmitting) return;
    isSubmitting = true; executeKey = executeKey || window.ResearchApp.idempotency(); $('#backtest-execute-confirm').disabled = true; setExecute({ status: 'READY' });
    state('#backtest-state', '正在请求服务端执行回测；结果是否可用以服务端运行状态为准。', 'loading');
    try {
      const payload = await request(`/api/v1/backtests/${encodeURIComponent(id)}/execute`, { method: 'POST', headers: { 'Idempotency-Key': executeKey } });
      state('#backtest-state', `服务端已受理回测执行：${window.ResearchApp.statusLabel ? window.ResearchApp.statusLabel(payload.data?.status, 'job') : (payload.data?.status || '状态待刷新')}。`, 'success', payload.request_id);
      executeKey = null; isSubmitting = false; executeConfirming = false; $('#backtest-execute-confirmation').hidden = true; await loadDetail(id);
    } catch (error) {
      renderFailure('#backtest-state', error, () => { executeConfirming = false; showExecuteConfirmation(currentRun || { run_id: root.dataset.runId, status: 'READY' }); });
      isSubmitting = false; $('#backtest-execute-confirm').disabled = false; setExecute({ status: 'READY' });
    }
  }

  $('#backtest-refresh').addEventListener('click', () => root.dataset.runId ? loadDetail(root.dataset.runId) : loadList());
  $('#backtest-execute').addEventListener('click', () => {
    if (executeConfirming) { executeConfirming = false; $('#backtest-execute-confirmation').hidden = true; setExecute({ status: 'READY' }); $('#backtest-execute').focus(); return; }
    showExecuteConfirmation(currentRun || { run_id: root.dataset.runId, status: 'READY' });
  });
  $('#backtest-execute-confirm').addEventListener('click', submitExecution);
  root.dataset.runId ? loadDetail(root.dataset.runId) : loadList();
}());
