(function () {
  'use strict';
  const root = document.querySelector('[data-page="daily-report"]');
  if (!root) return;
  const $ = (selector) => root.querySelector(selector);
  const refresh = $('#daily-report-refresh');
  const display = (value) => {
    if (value == null || value === '') return '--';
    return Array.isArray(value) ? value.join('、') : String(value);
  };
  const request = async (url) => window.ResearchApp.readJson(await window.ResearchApp.apiFetch(url));
  const state = (text, kind, requestId) => window.ResearchApp.renderState(
    $('#daily-report-state'), kind, text, requestId,
  );
  const list = (value) => Array.isArray(value) ? value : [];

  function showPairs(selector, pairs) {
    const target = $(selector);
    target.replaceChildren();
    const fragment = document.createDocumentFragment();
    pairs.forEach(([name, value]) => {
      const term = document.createElement('dt');
      const definition = document.createElement('dd');
      term.textContent = name;
      definition.textContent = display(value);
      fragment.append(term, definition);
    });
    target.append(fragment);
  }

  function renderRows(selector, rows, columns, emptyMessage) {
    const body = $(selector).querySelector('tbody');
    body.replaceChildren();
    if (!rows.length) {
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = columns.length;
      cell.className = 'muted';
      cell.textContent = emptyMessage;
      row.append(cell);
      body.append(row);
      return;
    }
    const fragment = document.createDocumentFragment();
    rows.forEach((item) => {
      const row = document.createElement('tr');
      columns.forEach((column) => {
        const cell = document.createElement('td');
        cell.textContent = display(column(item));
        row.append(cell);
      });
      fragment.append(row);
    });
    body.append(fragment);
  }

  function renderUnavailable(report, requestId) {
    const unavailable = $('#daily-report-unavailable');
    const reasons = list(report.unavailable_reasons);
    const unusable = report.result_usable === false || report.status === 'UNAVAILABLE';
    if (!unusable && !reasons.length) {
      unavailable.hidden = true;
      return false;
    }
    window.ResearchApp.renderState(
      unavailable,
      'unavailable',
      reasons.join('；') || '服务端标记日报结果不可用。请核对日终运行和数据质量后刷新日报。',
      requestId,
    );
    return true;
  }

  function render(report, requestId) {
    const unavailable = renderUnavailable(report, requestId);
    showPairs('#daily-report-summary', [
      ['报告日期', report.report_date],
      ['运行号', report.run_id],
      ['状态', report.status],
      ['结果可用', report.result_usable == null ? '--' : report.result_usable ? '是' : '否'],
      ['风险状态', report.risk_state || report.risk?.state],
    ]);
    showPairs('#daily-report-quality', [
      ['数据质量', report.data_quality],
      ['数据批次', report.data_batch_id || report.source_batch_id || report.batch_id],
      ['数据来源', report.source_name || report.data_source],
      ['数据版本', report.data_version || report.batch_version || report.source_version],
      ['市场开关', report.market_switch == null ? '--' : report.market_switch ? '开启' : '关闭'],
      ['实际成交录入', report.actual_execution_input == null ? '--' : report.actual_execution_input ? '已录入' : '未录入'],
      ['提示', report.notice],
    ]);
    const account = report.account || report.account_snapshot || {};
    const accountFields = ['account_id', 'cash', 'equity', 'market_value', 'drawdown', 'risk_state'];
    const accountPairs = accountFields.filter((key) => account[key] != null).map((key) => [key, account[key]]);
    showPairs('#daily-report-account', accountPairs.length ? accountPairs : [['账户快照', '服务端未提供账户快照。']]);
    renderRows('#daily-report-holdings', list(report.holdings), [
      (item) => item.symbol || item.security || item.security_id,
      (item) => item.quantity,
      (item) => item.available_quantity,
      (item) => item.avg_cost || item.cost,
      (item) => item.market_value || item.value,
    ], '暂无持仓快照。');
    renderRows('#daily-report-candidates', list(report.candidates), [
      (item) => item.symbol || item.security,
      (item) => item.condition || item.trigger_reasons || item.reason,
      (item) => item.score,
      (item) => item.risk_tags || item.risk_label || item.risk_state,
    ], '暂无候选标的。');
    renderRows('#daily-report-plans', list(report.order_plans || report.plans), [
      (item) => item.plan_no || item.plan_id,
      (item) => item.side,
      (item) => item.quantity,
      (item) => item.status,
      (item) => item.execution_date || item.expires_at,
    ], '暂无人工确认计划。');
    return unavailable;
  }

  function renderError(error) {
    const isPermission = error.status === 401 || error.status === 403;
    state(
      isPermission ? '当前账户没有查看日报的权限；请使用有权限的账户或联系管理员。' : `${window.ResearchApp.errorMessage(error.status, error.payload)} 请刷新日报重试。`,
      isPermission ? 'permission' : 'error',
      error.payload?.request_id,
    );
  }

  async function load() {
    refresh.disabled = true;
    refresh.textContent = '正在刷新…';
    state('正在加载日报、风险和数据可用性…', 'loading');
    try {
      const payload = await request(root.dataset.apiUrl);
      const unavailable = render(payload.data || {}, payload.request_id);
      state(unavailable ? '日报已加载，但服务端标记部分结果不可用；请先处理不可用原因。' : '日报已加载。', unavailable ? 'unavailable' : 'success', payload.request_id);
    } catch (error) {
      renderError(error);
    } finally {
      refresh.disabled = false;
      refresh.textContent = '刷新日报';
    }
  }

  refresh.addEventListener('click', load);
  load();
}());
