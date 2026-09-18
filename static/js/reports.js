(function () {
  'use strict';
  const root = document.querySelector('[data-page="reports"]');
  if (!root) return;
  const $ = (selector) => root.querySelector(selector);
  const refresh = $('#reports-refresh');
  const loadButton = $('#report-load');
  const select = $('#report-run-select');
  const display = (value) => value == null || value === '' ? '--' : String(value);
  const statusLabel = (value, domain) => window.ResearchApp.statusLabel ? window.ResearchApp.statusLabel(value, domain) : display(value);
  const request = async (url) => window.ResearchApp.readJson(await window.ResearchApp.apiFetch(url));
  const state = (text, kind, requestId) => window.ResearchApp.renderState($('#reports-state'), kind, text, requestId);
  let reportRequest = 0;
  let listRequest = 0;

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

  function showTable(selector, value, emptyText) {
    const body = $(`${selector} tbody`);
    body.replaceChildren();
    const entries = value && typeof value === 'object'
      ? (Array.isArray(value) ? value.map((item, index) => [String(index + 1), item]) : Object.entries(value))
      : value == null || value === '' ? [] : [['值', value]];
    (entries.length ? entries : [['说明', emptyText]]).forEach(([key, item]) => {
      const row = document.createElement('tr'); const term = document.createElement('th'); term.scope = 'row'; term.textContent = key;
      const definition = document.createElement('td'); definition.textContent = item && typeof item === 'object' ? JSON.stringify(item) : display(item); row.append(term, definition); body.append(row);
    });
  }

  function selectionState(kind, text, requestId) {
    window.ResearchApp.renderState($('#report-selection-state'), kind, text, requestId);
  }

  function clearReport() {
    reportRequest += 1;
    loadButton.textContent = '查看报告';
    $('#report-content').hidden = false;
    $('#report-summary-placeholder').hidden = false;
    $('#report-metrics-placeholder').hidden = false;
    showPairs('#report-summary', []);
    showPairs('#report-metrics', []);
    $('#report-metrics-table tbody').replaceChildren();
    $('#report-cost-table tbody').replaceChildren();
    $('#report-deviation-table tbody').replaceChildren();
    $('#report-recovery').textContent = '尚未选择报告。';
    $('#report-raw-json').textContent = '尚未选择报告。';
    $('#report-unavailable').hidden = true;
  }

  function errorState(error, objectName) {
    if (error.status === 401 || error.status === 403) {
      return ['permission', `当前账户没有查看${objectName}的权限；请使用有权限的账户或联系管理员。`];
    }
    return ['error', `${window.ResearchApp.errorMessage(error.status, error.payload)} 请刷新后重试。`];
  }

  async function loadRuns() {
    const listNumber = ++listRequest;
    clearReport();
    $('#reports-empty').hidden = true;
    $('#report-picker').hidden = false;
    refresh.disabled = true;
    refresh.textContent = '正在刷新…';
    select.disabled = true;
    loadButton.disabled = true;
    state('正在加载可查看的回测运行…', 'loading');
    try {
      const runs = [];
      let payload;
      for (let page = 1; ; page += 1) {
        payload = await request(`${root.dataset.apiUrl}?page=${page}&page_size=200`);
        if (listNumber !== listRequest) return;
        const items = payload.data?.items || [];
        runs.push(...items);
        if (!items.length || runs.length >= Number(payload.data?.total || 0)) break;
      }
      const previousRunId = select.value;
      select.replaceChildren();
      select.append(new Option(runs.length ? '请选择服务端保存的回测运行' : '暂无可查看的回测运行', ''));
      runs.forEach((run) => {
        const label = [run.run_id, `${run.start_date || '--'} 至 ${run.end_date || '--'}`, statusLabel(run.status, 'job'), run.result_usable ? '结果可用' : '结果不可用'].join(' | ');
        select.append(new Option(label, run.run_id));
      });
      if (runs.some((run) => run.run_id === previousRunId)) select.value = previousRunId;
      select.disabled = !runs.length;
      loadButton.disabled = !select.value;
      if (runs.length) {
        selectionState('success', `已加载 ${runs.length} 条回测运行。选择一条后查看服务端报告。`, payload.request_id);
        $('#reports-state').hidden = true;
      } else {
        clearReport();
        $('#reports-state').hidden = true;
        $('#reports-empty').hidden = false;
      }
    } catch (error) {
      if (listNumber !== listRequest) return;
      const [kind, text] = errorState(error, '回测运行');
      select.replaceChildren(new Option('无法获取可查看的运行', ''));
      clearReport();
      selectionState(kind, text, error.payload?.request_id);
      $('#reports-state').hidden = true;
    } finally {
      if (listNumber !== listRequest) return;
      refresh.disabled = false;
      refresh.textContent = '刷新运行';
    }
  }

  async function loadReport() {
    const runId = select.value;
    if (!runId) {
      selectionState('unavailable', '请先选择服务端保存的回测运行。');
      return;
    }
    clearReport();
    const requestNumber = reportRequest;
    loadButton.disabled = true;
    loadButton.textContent = '正在加载…';
    selectionState('loading', `正在加载运行 ${runId} 的服务端报告…`);
    try {
      const [payload, runPayload] = await Promise.all([
        request(`/api/v1/backtests/${encodeURIComponent(runId)}/report`),
        request(`/api/v1/backtests/${encodeURIComponent(runId)}`),
      ]);
      if (requestNumber !== reportRequest || runId !== select.value) return;
      const report = payload.data || {};
      const run = runPayload.data || {};
      if ((report.run_id && report.run_id !== runId) || run.run_id !== runId) throw new Error('运行标识不一致，请刷新后重试。');
      const unavailableReasons = Array.isArray(report.unavailable_reasons) ? report.unavailable_reasons : [];
      const unavailable = report.result_usable !== true || run.result_usable !== true || report.status === 'UNAVAILABLE' || unavailableReasons.length;
      showPairs('#report-summary', [
        ['运行号', report.run_id || runId],
        ['状态', statusLabel(report.status, 'job')],
        ['结果可用', report.result_usable == null ? '--' : report.result_usable ? '是' : '否'],
        ['数据批次', report.data_batch_id || run.data_batch_id],
        ['策略版本', report.strategy_version_id || run.strategy_version_id],
        ['成本版本', run.cost_config_id],
        ['规则版本', run.rule_config_id],
        ['基准', run.benchmark_symbol],
        ['快照哈希', report.snapshot_hash],
      ]);
      const metrics = Object.entries(report.metrics || {});
      $('#report-summary-placeholder').hidden = true;
      $('#report-metrics-placeholder').hidden = true;
      showPairs('#report-metrics', metrics.length ? metrics : [['指标', '服务端尚未提供可用指标。']]);
      showTable('#report-metrics-table', report.metrics, '服务端尚未提供可用绩效指标。');
      const unavailableTarget = $('#report-unavailable');
      if (unavailable) {
        window.ResearchApp.renderState(unavailableTarget, 'unavailable', unavailableReasons.join('；') || '服务端标记报告结果不可用。请核对回测运行和数据版本后刷新。', payload.request_id);
      } else {
        unavailableTarget.hidden = true;
      }
      const deviation = report.plan_actual_deviation ?? report.deviations ?? null;
      const cost = report.cost ?? null;
      showTable('#report-deviation-table', deviation, '报告未提供关联的计划与人工实际成交偏差。');
      showTable('#report-cost-table', cost, '报告未提供模拟成本明细。');
      $('#report-raw-json').textContent = JSON.stringify({ report, run }, null, 2);
      $('#report-recovery').textContent = unavailable ? `${unavailableReasons.join('；') || '服务端标记报告结果不可用。'} 恢复路径：核对数据批次、策略/规则版本和运行状态后刷新；不可用结果不会被当作可用绩效。` : '当前报告可用；如需追溯字段来源，请展开“查看原始服务端证据”。';
      $('#report-run-link').href = `/backtests/${encodeURIComponent(runId)}/view`;
      $('#report-content').hidden = false;
      const resultText = unavailable ? '报告已加载，但服务端标记结果不可用；请先处理不可用原因。' : `报告 ${report.run_id || runId} 已加载。`;
      selectionState(unavailable ? 'unavailable' : 'success', resultText, payload.request_id);
      $('#reports-state').hidden = true;
    } catch (error) {
      if (requestNumber !== reportRequest) return;
      clearReport();
      const [kind, text] = errorState(error, '该回测报告');
      selectionState(kind, text, error.payload?.request_id);
      loadButton.disabled = !select.value;
      loadButton.textContent = '查看报告';
    } finally {
      if (requestNumber !== reportRequest) return;
      loadButton.disabled = !select.value;
      loadButton.textContent = '查看报告';
    }
  }

  select.addEventListener('change', () => {
    clearReport();
    $('#reports-state').hidden = true;
    loadButton.textContent = '查看报告';
    loadButton.disabled = !select.value;
    selectionState('unavailable', select.value ? '已选择运行。请查看服务端报告。' : '请先选择服务端保存的回测运行。');
  });
  refresh.addEventListener('click', loadRuns);
  loadButton.addEventListener('click', loadReport);
  clearReport();
  loadRuns();
}());
