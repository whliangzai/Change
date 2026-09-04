(function () {
  const root = document.querySelector('[data-page="daily-flow"]');
  if (!root) return;
  const $ = (selector) => root.querySelector(selector);
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const roles = () => {
    try {
      const token = window.ResearchApp.getToken();
      const raw = token && token.split('.')[1];
      const value = raw && JSON.parse(atob(raw.replace(/-/g, '+').replace(/_/g, '/').padEnd(raw.length + (4 - raw.length % 4) % 4, '=')));
      return value && Array.isArray(value.roles) ? value.roles : [];
    } catch (_) { return []; }
  };
  const request = async (url, options) => window.ResearchApp.readJson(await window.ResearchApp.apiFetch(url, options));
  const state = (text, kind, requestId) => window.ResearchApp.renderState($('#daily-flow-state'), kind || '', text, requestId);
  const iso = (value) => value ? new Date(value).toISOString() : value;
  const admin = roles().includes('ADMIN');
  $('#daily-flow-permission').textContent = admin ? '当前账号具备管理员权限。' : '需要管理员权限才能执行日终；服务端会拒绝无权请求。';
  $('#daily-flow-submit').disabled = !admin;
  async function loadBatches() {
    try {
      const payload = await request('/api/v1/data/batches?page=1&page_size=200');
      const items = (payload.data && payload.data.items || []).filter((item) => ['AVAILABLE', 'WARNING_AVAILABLE'].includes(item.quality_status));
      const select = $('#daily-flow-batch');
      select.replaceChildren(new Option(items.length ? '请选择可用数据批次' : '暂无通过质量门禁的数据批次', ''));
      items.forEach((item) => select.add(new Option(`${item.data_date || ''} · ${item.batch_id}`, item.batch_id)));
      if (!items.length) state('暂无通过质量门禁的数据批次。请先导入并修复数据质量问题。', 'error');
    } catch (error) { state(error.message || '无法加载数据批次。', 'error'); }
  }
  async function loadStrategies() {
    try {
      const payload = await request('/api/v1/strategies?status=PUBLISHED&page=1&page_size=200');
      const items = (payload.data && payload.data.items || []).filter((item) => item.status === 'PUBLISHED');
      const select = $('#daily-flow-strategy');
      select.replaceChildren(new Option(items.length ? '请选择已发布策略版本' : '暂无可选择的已发布策略', ''));
      items.forEach((item) => select.add(new Option(`${item.name || item.strategy_version_id} · ${item.strategy_version_id}`, item.strategy_version_id)));
      if (!items.length) state('暂无可选择的已发布策略。请先创建并发布策略版本。', 'error');
    } catch (error) { state(error.message || '无法加载已发布策略。请先确认策略服务可用。', 'error'); }
  }
  $('#daily-flow-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget).entries());
    values.information_cutoff_at = iso(values.information_cutoff_at);
    if (!values.data_batch_id || !values.strategy_version_id) return state('请选择可用数据批次和已发布策略版本。', 'error');
    $('#daily-flow-submit').disabled = true;
    $('#daily-flow-empty').hidden = true;
    state('正在执行日终…', 'loading');
    try {
      const payload = await request('/api/v1/daily-flows', { method: 'POST', body: values });
      const result = payload.data || {};
      $('#daily-flow-result').innerHTML = [['运行状态', result.status], ['运行 ID', result.run_id], ['候选', (result.signal_symbols || []).join('、') || '无'], ['T+1 计划数', result.plan_count]].map(([key, value]) => `<dt>${key}</dt><dd><code>${esc(value ?? '未提供')}</code></dd>`).join('');
      const date = values.as_of_date;
      const next = (result.plan_execution_dates || [])[0];
      const links = $('#daily-flow-links');
      links.innerHTML = `<a class="button-link" href="/daily-reports/${encodeURIComponent(date)}">查看日报与候选</a>${next ? `<a class="button-link" href="/order-plans?execution_date=${encodeURIComponent(next)}">查看 T+1 计划</a>` : ''}`;
      links.hidden = false;
      if (!(result.signal_symbols || []).length || !result.plan_count) $('#daily-flow-empty').hidden = false;
      state(`日终已提交，运行状态：${result.status || '已接受'}。`, result.status === 'SUCCEEDED' ? 'success' : 'loading', payload.request_id);
    } catch (error) {
      state(error.message || '日终无法执行；数据不足、质量失败或前置版本不可用时不会生成结果。', 'error');
      $('#daily-flow-submit').disabled = !admin;
    }
  });
  loadBatches();
  loadStrategies();
}());
