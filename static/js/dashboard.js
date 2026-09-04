(function () {
  const root = document.querySelector('[data-page="dashboard"]');
  if (!root) return;
  const state = document.getElementById('dashboard-state');
  const fill = (id, values) => {
    document.getElementById(id).innerHTML = values.map(([name, value]) =>
      `<dt>${name}</dt><dd>${String(value ?? '未提供')}</dd>`).join('');
  };
  async function request(path) {
    return window.ResearchApp.readJson(await window.ResearchApp.apiFetch(path));
  }
  async function load() {
    window.ResearchApp.renderState(state, 'loading', '正在加载运行摘要…');
    const date = encodeURIComponent(root.dataset.reportDate);
    try {
      const [batches, backtests, report] = await Promise.all([
        request('/api/v1/data/batches?page=1&page_size=1'),
        request('/api/v1/backtests?page=1&page_size=1'),
        request(`/api/v1/daily-reports/${date}`),
      ]);
      const batch = (batches.data.items || [])[0];
      const run = (backtests.data.items || [])[0];
      const daily = report.data || {};
      fill('dashboard-data', [['批次数', batches.data.total], ['最近状态', batch && batch.quality_status], ['数据日期', batch && batch.data_date]]);
      fill('dashboard-backtests', [['运行数', backtests.data.total], ['最近状态', run && run.status], ['结果可用', run ? (run.result_usable ? '是' : '否') : '未提供']]);
      fill('dashboard-daily', [['数据质量', daily.data_quality], ['市场开关', daily.market_switch], ['风险状态', daily.risk_state], ['运行号', daily.run_id]]);
      window.ResearchApp.renderState(state, 'success', '运行摘要已加载', report.request_id);
    } catch (error) {
      window.ResearchApp.renderState(state, 'error', window.ResearchApp.errorMessage(error.status, error.payload));
    }
  }
  document.getElementById('dashboard-refresh').addEventListener('click', load);
  load();
}());
