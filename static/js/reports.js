(function () {
  'use strict';
  const root = document.querySelector('[data-page="reports"]');
  if (!root) return;
  const $ = (selector) => root.querySelector(selector);
  const refresh = $('#reports-refresh');
  const loadButton = $('#report-load');
  const select = $('#report-run-select');
  const display = (value) => value == null || value === '' ? '--' : String(value);
  const request = async (url) => window.ResearchApp.readJson(await window.ResearchApp.apiFetch(url));
  const state = (text, kind, requestId) => window.ResearchApp.renderState($('#reports-state'), kind, text, requestId);

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

  function selectionState(kind, text, requestId) {
    window.ResearchApp.renderState($('#report-selection-state'), kind, text, requestId);
  }

  function clearReport() {
    showPairs('#report-summary', [['运行版本与可用性', '尚未选择报告。']]);
    showPairs('#report-metrics', [['策略绩效与基准比较', '尚未选择报告。']]);
    $('#report-deviation').textContent = '尚未选择报告。';
    $('#report-unavailable').hidden = true;
  }

  function errorState(error, objectName) {
    if (error.status === 401 || error.status === 403) {
      return ['permission', `当前账户没有查看${objectName}的权限；请使用有权限的账户或联系管理员。`];
    }
    return ['error', `${window.ResearchApp.errorMessage(error.status, error.payload)} 请刷新后重试。`];
  }

  async function loadRuns() {
    refresh.disabled = true;
    refresh.textContent = '正在刷新…';
    select.disabled = true;
    loadButton.disabled = true;
    state('正在加载可查看的回测运行…', 'loading');
    try {
      const payload = await request(`${root.dataset.apiUrl}?page=1&page_size=200`);
      const runs = Array.isArray(payload.data?.items) ? payload.data.items : [];
      const previousRunId = select.value;
      select.replaceChildren();
      select.append(new Option(runs.length ? '请选择服务端保存的回测运行' : '暂无可查看的回测运行', ''));
      runs.forEach((run) => {
        const label = [run.run_id, run.status || '--', run.result_usable ? '结果可用' : '结果不可用'].join(' | ');
        select.append(new Option(label, run.run_id));
      });
      if (runs.some((run) => run.run_id === previousRunId)) select.value = previousRunId;
      select.disabled = !runs.length;
      loadButton.disabled = !select.value;
      if (runs.length) {
        selectionState('success', `已加载 ${runs.length} 条回测运行。选择一条后查看服务端报告。`, payload.request_id);
        state('回测运行已加载。', 'success', payload.request_id);
      } else {
        clearReport();
        selectionState('unavailable', '暂无可查看的回测运行。请先创建并完成研究回测后刷新。', payload.request_id);
        state('暂无可查看的回测运行。', 'unavailable', payload.request_id);
      }
    } catch (error) {
      const [kind, text] = errorState(error, '回测运行');
      select.replaceChildren(new Option('无法获取可查看的运行', ''));
      clearReport();
      selectionState(kind, text, error.payload?.request_id);
      state(text, kind, error.payload?.request_id);
    } finally {
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
    loadButton.disabled = true;
    loadButton.textContent = '正在加载…';
    selectionState('loading', `正在加载运行 ${runId} 的服务端报告…`);
    try {
      const payload = await request(`/api/v1/backtests/${encodeURIComponent(runId)}/report`);
      const report = payload.data || {};
      const unavailableReasons = Array.isArray(report.unavailable_reasons) ? report.unavailable_reasons : [];
      const unavailable = report.result_usable === false || report.status === 'UNAVAILABLE' || unavailableReasons.length;
      showPairs('#report-summary', [
        ['运行号', report.run_id || runId],
        ['状态', report.status],
        ['结果可用', report.result_usable == null ? '--' : report.result_usable ? '是' : '否'],
        ['数据批次', report.data_batch_id],
        ['策略版本', report.strategy_version_id],
        ['快照哈希', report.snapshot_hash],
      ]);
      const metrics = Object.entries(report.metrics || {});
      showPairs('#report-metrics', metrics.length ? metrics : [['指标', '服务端尚未提供可用指标。']]);
      const unavailableTarget = $('#report-unavailable');
      if (unavailable) {
        window.ResearchApp.renderState(unavailableTarget, 'unavailable', unavailableReasons.join('；') || '服务端标记报告结果不可用。请核对回测运行和数据版本后刷新。', payload.request_id);
      } else {
        unavailableTarget.hidden = true;
      }
      const deviation = report.plan_actual_deviation ?? report.deviations ?? report.cost ?? null;
      $('#report-deviation').textContent = deviation == null ? '服务端报告未提供计划/实际偏差或成本明细。' : JSON.stringify(deviation, null, 2);
      const resultText = unavailable ? '报告已加载，但服务端标记结果不可用；请先处理不可用原因。' : `报告 ${report.run_id || runId} 已加载。`;
      selectionState(unavailable ? 'unavailable' : 'success', resultText, payload.request_id);
      state(resultText, unavailable ? 'unavailable' : 'success', payload.request_id);
    } catch (error) {
      const [kind, text] = errorState(error, '该回测报告');
      selectionState(kind, text, error.payload?.request_id);
      state(text, kind, error.payload?.request_id);
    } finally {
      loadButton.disabled = !select.value;
      loadButton.textContent = '查看报告';
    }
  }

  select.addEventListener('change', () => {
    loadButton.disabled = !select.value;
    selectionState('unavailable', select.value ? '已选择运行。请查看服务端报告。' : '请先选择服务端保存的回测运行。');
  });
  refresh.addEventListener('click', loadRuns);
  loadButton.addEventListener('click', loadReport);
  clearReport();
  loadRuns();
}());
