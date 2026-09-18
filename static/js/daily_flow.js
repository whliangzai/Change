(function () {
  const root = document.querySelector('[data-page="daily-flow"]');
  if (!root) return;
  const $ = (selector) => root.querySelector(selector);
  const form = $('#daily-flow-form');
  const submit = $('#daily-flow-submit');
  let isSubmitting = false;
  const available = { batch: false, strategy: false };
  let confirming = false;
  let pendingValues = null;
  const fieldIds = { data_batch_id: 'data_batch_id', strategy_version_id: 'strategy_version_id', cost_config_id: 'cost_config_id', rule_config_id: 'rule_config_id', as_of_date: 'as_of_date', information_cutoff_at: 'information_cutoff_at', initial_equity: 'initial_equity', max_investment_ratio: 'max_investment_ratio' };
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const state = (text, kind, requestId) => window.ResearchApp.renderState($('#daily-flow-state'), kind || '', text, requestId);
  const request = async (url, options) => window.ResearchApp.readJson(await window.ResearchApp.apiFetch(url, options));
  const iso = (value) => value ? new Date(value).toISOString() : value;
  const roles = () => { try { const token = window.ResearchApp.getToken(); const raw = token && token.split('.')[1]; const claims = raw && JSON.parse(atob(raw.replace(/-/g, '+').replace(/_/g, '/').padEnd(raw.length + (4 - raw.length % 4) % 4, '='))); return claims && Array.isArray(claims.roles) ? claims.roles : []; } catch (_) { return []; } };
  const admin = roles().includes('ADMIN');
  const clearFieldErrors = () => {
    root.querySelectorAll('.field-error').forEach((element) => { element.hidden = true; element.textContent = ''; });
    form.querySelectorAll('[aria-invalid="true"]').forEach((element) => element.removeAttribute('aria-invalid'));
    $('#daily-flow-form-error').hidden = true;
  };
  const setFieldError = (field, text) => {
    const input = form.elements.namedItem(field);
    const target = $(`#daily-flow-${fieldIds[field]}-error`);
    if (input) input.setAttribute('aria-invalid', 'true');
    if (target) { target.textContent = text; target.hidden = false; }
  };
  const showFormError = (text) => { const element = $('#daily-flow-form-error'); element.textContent = text; element.hidden = false; };
  const mapServerErrors = (error) => {
    const details = error.payload?.error?.details;
    if (!Array.isArray(details)) return;
    details.forEach((detail) => setFieldError(detail.field || (Array.isArray(detail.loc) ? detail.loc.at(-1) : ''), detail.reason || detail.msg || error.message));
  };
  const statusLabel = (value, domain) => window.ResearchApp.statusLabel ? window.ResearchApp.statusLabel(value, domain) : String(value ?? '--');
  const appendSummary = (target, pairs) => {
    target.replaceChildren();
    pairs.forEach(([label, value]) => {
      const term = document.createElement('dt'); term.textContent = label;
      const definition = document.createElement('dd'); definition.textContent = String(value ?? '--'); target.append(term, definition);
    });
  };
  const setConfirmationMode = (enabled) => {
    confirming = enabled;
    form.querySelectorAll('input, select, textarea').forEach((element) => { if (element.id !== 'daily-flow-submit' && element.id !== 'daily-flow-confirm') element.disabled = enabled; });
    $('#daily-flow-confirmation').hidden = !enabled;
    submit.textContent = enabled ? '返回修改' : submit.dataset.submitLabel;
  };
  const updateAvailability = () => {
    const ready = admin && available.batch && available.strategy;
    submit.disabled = isSubmitting || !ready;
    if (!isSubmitting && !ready) state(admin ? '日终不可用：需要通过质量门禁的数据批次和已发布策略。' : '当前会话未显示管理员权限，无法执行日终。', admin ? 'unavailable' : 'permission');
  };
  async function loadBatches() {
    const select = $('#daily-flow-batch'); select.disabled = true;
    try {
      const payload = await request('/api/v1/data/batches?page=1&page_size=200');
      const items = (payload.data?.items || []).filter((item) => ['AVAILABLE', 'WARNING_AVAILABLE'].includes(item.quality_status));
      select.replaceChildren(new Option(items.length ? '请选择可用数据批次' : '暂无通过质量门禁的数据批次', ''));
      items.forEach((item) => select.add(new Option(`${item.data_date || '--'} · ${item.batch_id}`, item.batch_id)));
      available.batch = items.length > 0; select.disabled = !available.batch;
      if (!available.batch) state('暂无通过质量门禁的数据批次。请先导入并修复数据质量问题。', 'unavailable', payload.request_id);
    } catch (error) { available.batch = false; state(error.message || '无法加载数据批次。', error.status === 403 ? 'permission' : 'error', error.payload?.request_id); }
    updateAvailability();
  }
  async function loadStrategies() {
    const select = $('#daily-flow-strategy'); select.disabled = true;
    try {
      const payload = await request('/api/v1/strategies?status=PUBLISHED&page=1&page_size=200');
      const items = (payload.data?.items || []).filter((item) => item.status === 'PUBLISHED');
      select.replaceChildren(new Option(items.length ? '请选择已发布策略版本' : '暂无可选择的已发布策略', ''));
      items.forEach((item) => select.add(new Option(`${item.name || item.strategy_version_id} · ${item.strategy_version_id}`, item.strategy_version_id)));
      available.strategy = items.length > 0; select.disabled = !available.strategy;
      if (!available.strategy) state('暂无可选择的已发布策略。请先创建并发布策略版本。', 'unavailable', payload.request_id);
    } catch (error) { available.strategy = false; state(error.message || '无法加载已发布策略。请确认策略服务可用。', error.status === 403 ? 'permission' : 'error', error.payload?.request_id); }
    updateAvailability();
  }
  $('#daily-flow-permission').textContent = admin ? '当前会话显示管理员权限；服务端仍会在提交时执行 RBAC、审计和幂等处理。' : '当前会话未显示管理员权限；服务端会拒绝无权请求。';
  $('#daily-flow-permission').className = `state ${admin ? 'success' : 'permission'}`;
  async function submitConfirmed() {
    const values = pendingValues;
    if (!values || isSubmitting) return;
    isSubmitting = true; updateAvailability(); $('#daily-flow-confirm').disabled = true; submit.textContent = '正在执行日终…'; $('#daily-flow-empty').hidden = true;
    state('正在执行日终；只会生成研究候选与 T+1 人工计划，请勿重复提交。', 'loading');
    try {
      const payload = await request('/api/v1/daily-flows', { method: 'POST', body: values });
      const result = payload.data || {};
      $('#daily-flow-result').innerHTML = [['运行状态', statusLabel(result.status, 'job')], ['运行 ID', result.run_id], ['候选', (result.signal_symbols || []).join('、') || '无'], ['T+1 计划数', result.plan_count]].map(([key, value]) => `<dt>${key}</dt><dd><code>${esc(value ?? '--')}</code></dd>`).join('');
      const next = (result.plan_execution_dates || [])[0]; const links = $('#daily-flow-links');
      links.innerHTML = `<a class="button-link" href="/daily-reports/${encodeURIComponent(values.as_of_date)}">查看日报与候选</a>${next ? `<a class="button-link" href="/order-plans?execution_date=${encodeURIComponent(next)}">查看 T+1 人工计划</a>` : ''}`; links.hidden = false;
      if (!(result.signal_symbols || []).length || !result.plan_count) $('#daily-flow-empty').hidden = false;
      state(`日终已由服务端受理，运行状态：${statusLabel(result.status || 'QUEUED', 'job')}。这不代表已下单或已成交。`, result.status === 'SUCCEEDED' ? 'success' : 'loading', payload.request_id);
      setConfirmationMode(false); pendingValues = null;
    } catch (error) {
      mapServerErrors(error); showFormError(error.message || '日终无法执行；请检查数据、版本与服务状态。');
      state(error.message || '日终无法执行；数据不足、质量失败或前置版本不可用时不会生成结果。', error.status === 403 ? 'permission' : (error.status === 503 ? 'unavailable' : 'error'), error.payload?.request_id);
      $('#daily-flow-confirm').disabled = false;
    }
    isSubmitting = false; updateAvailability(); submit.textContent = confirming ? '返回修改' : submit.dataset.submitLabel;
  }
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (isSubmitting || submit.disabled) return;
    if (confirming) { setConfirmationMode(false); pendingValues = null; state('已返回修改，尚未提交日终。', 'unavailable'); submit.focus(); return; }
    clearFieldErrors();
    const values = Object.fromEntries(new FormData(form).entries());
    const required = ['data_batch_id', 'strategy_version_id', 'cost_config_id', 'rule_config_id', 'as_of_date', 'information_cutoff_at', 'initial_equity', 'max_investment_ratio'];
    const missing = required.filter((field) => !String(values[field] || '').trim());
    if (missing.length) { missing.forEach((field) => setFieldError(field, '此项为必填。')); showFormError('请检查标记的字段后重试。'); form.elements.namedItem(missing[0])?.focus(); return; }
    values.information_cutoff_at = iso(values.information_cutoff_at);
    pendingValues = values;
    appendSummary($('#daily-flow-confirmation-summary'), [
      ['对象', '日终研究运行'], ['日期', values.as_of_date], ['数据批次 / 策略版本', `${values.data_batch_id} / ${values.strategy_version_id}`], ['成本 / 规则版本', `${values.cost_config_id} / ${values.rule_config_id}`], ['关键参数', `初始权益 ${values.initial_equity}；最大投资比例 ${values.max_investment_ratio}`], ['信息截点', values.information_cutoff_at],
    ]);
    setConfirmationMode(true); state('请核对摘要；点击“确认提交”后才会发送日终请求。', 'warning');
  });
  $('#daily-flow-confirm').addEventListener('click', submitConfirmed);
  submit.disabled = true;
  loadBatches();
  loadStrategies();
}());
