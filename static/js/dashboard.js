(function () {
  'use strict';
  const root = document.querySelector('[data-page="dashboard"]');
  if (!root) return;

  const $ = (id) => document.getElementById(id);
  const state = $('dashboard-state');
  const refresh = $('dashboard-refresh');
  const display = (value) => value == null || value === '' ? '--' : String(value);
  const request = async (path) => window.ResearchApp.readJson(await window.ResearchApp.apiFetch(path));
  const statusLabel = (value, domain) => window.ResearchApp.statusLabel ? window.ResearchApp.statusLabel(value, domain) : String(value ?? '--');

  function showPairs(id, pairs) {
    const target = $(id);
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

  function showPanelState(id, kind, text, requestId) {
    const target = $(id);
    const panel = target.closest ? target.closest('.panel') : null;
    if (!text) {
      target.hidden = true;
      if (panel) panel.classList.remove('needs-action');
      return;
    }
    window.ResearchApp.renderState(target, kind, text, requestId);
    if (panel) panel.classList.add('needs-action');
  }

  function hideLoading(name) {
    const skeleton = $(`dashboard-${name}-loading`);
    if (skeleton) skeleton.hidden = true;
  }

  function errorState(error, objectName) {
    if (error.status === 401 || error.status === 403) {
      return ['permission', `当前账户没有查看${objectName}的权限；请使用有权限的账户或联系管理员。`];
    }
    return ['error', `${window.ResearchApp.errorMessage(error.status, error.payload)} 请刷新摘要重试。`];
  }

  async function load() {
    refresh.disabled = true;
    refresh.textContent = '正在刷新…';
    window.ResearchApp.renderState(state, 'loading', '正在加载数据可用性、回测运行和日报风险…');
    const reportDate = encodeURIComponent(root.dataset.reportDate);
    const requests = [
      ['data', '数据批次', request('/api/v1/data/batches?page=1&page_size=1')],
      ['backtests', '回测运行', request('/api/v1/backtests?page=1&page_size=1')],
      ['daily', '日报', request(`/api/v1/daily-reports/${reportDate}`)],
    ];
    const results = await Promise.allSettled(requests.map((entry) => entry[2]));
    const failures = [];

    results.forEach((result, index) => {
      const [name, objectName] = requests[index];
      if (result.status === 'rejected') {
        hideLoading(name === 'backtests' ? 'backtests' : name);
        const [kind, text] = errorState(result.reason, objectName);
        const panel = name === 'backtests' ? 'backtest' : name;
        showPanelState(`dashboard-${panel}-availability`, kind, text, result.reason.payload?.request_id);
        failures.push(objectName);
        return;
      }

      const payload = result.value;
      if (name === 'data') {
        hideLoading('data');
        const page = payload.data || {};
        const batch = Array.isArray(page.items) ? page.items[0] : null;
        showPairs('dashboard-data', [
          ['批次数', page.total],
          ['最近状态', statusLabel(batch?.quality_status, 'quality')],
          ['数据日期', batch?.data_date],
          ['数据来源', batch?.source_name],
          ['版本', batch?.version],
        ]);
        showPanelState(
          'dashboard-data-availability',
          'unavailable',
          Number(page.total || 0) ? '' : '暂无可用数据批次。请先完成授权数据导入或调整数据范围。',
          payload.request_id,
        );
      } else if (name === 'backtests') {
        hideLoading('backtests');
        const page = payload.data || {};
        const run = Array.isArray(page.items) ? page.items[0] : null;
        showPairs('dashboard-backtests', [
          ['运行数', page.total],
          ['最近状态', statusLabel(run?.status, 'job')],
          ['结果可用', run ? (run.result_usable ? '是' : '否') : '--'],
          ['数据批次', run?.data_batch_id],
          ['策略版本', run?.strategy_version_id],
        ]);
        showPanelState(
          'dashboard-backtest-availability',
          'unavailable',
          Number(page.total || 0) ? '' : '暂无可查看的回测运行。请先创建并完成研究回测。',
          payload.request_id,
        );
      } else {
        hideLoading('daily');
        const daily = payload.data || {};
        if (daily.report_date) $('dashboard-daily-link').href = `/daily-reports/${encodeURIComponent(daily.report_date)}`;
        const unavailable = Array.isArray(daily.unavailable_reasons) ? daily.unavailable_reasons : [];
        const unusable = daily.result_usable === false || daily.status === 'UNAVAILABLE';
        showPairs('dashboard-daily', [
          ['报告日期', daily.report_date],
          ['数据质量', statusLabel(daily.data_quality, 'quality')],
          ['市场开关', daily.market_switch],
          ['风险状态', statusLabel(daily.risk_state, 'risk')],
          ['运行号', daily.run_id],
        ]);
        showPanelState(
          'dashboard-daily-availability',
          'unavailable',
          unusable ? (unavailable.join('；') || '日报结果当前不可用。请核对日终运行与数据质量后刷新。') : '',
          payload.request_id,
        );
      }
    });
    hideLoading('tasks');

    if (failures.length) {
      window.ResearchApp.renderState(state, 'error', `未能加载${failures.join('、')}；其余摘要已保留，可刷新重试。`);
    } else {
      window.ResearchApp.renderState(state, 'success', '运行摘要已加载。优先处理标记为不可用或风险关注的项目。');
    }
    refresh.disabled = false;
    refresh.textContent = '刷新摘要';
  }

  refresh.addEventListener('click', load);
  load();
}());
