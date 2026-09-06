(function () {
  const root = document.querySelector('[data-page="backtest-create"]');
  if (!root) return;
  const $ = (selector) => root.querySelector(selector);
  const form = $('#backtest-form');
  const submit = $('#backtest-submit');
  let isSubmitting = false;
  let hasAvailableBatch = false;
  const fieldIds = { data_batch_id: 'data_batch_id', strategy_version_id: 'strategy_version_id', cost_config_id: 'cost_config_id', rule_config_id: 'rule_config_id', benchmark_symbol: 'benchmark_symbol', initial_equity: 'initial_equity' };
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const state = (text, kind, requestId) => window.ResearchApp.renderState($('#backtest-create-state'), kind || '', text, requestId);
  const request = async (url, options) => window.ResearchApp.readJson(await window.ResearchApp.apiFetch(url, options));
  const clearFieldErrors = () => {
    root.querySelectorAll('.field-error').forEach((element) => { element.hidden = true; element.textContent = ''; });
    form.querySelectorAll('[aria-invalid="true"]').forEach((element) => element.removeAttribute('aria-invalid'));
    $('#backtest-form-error').hidden = true;
  };
  const setFieldError = (field, text) => {
    const target = $(`#backtest-${fieldIds[field] || 'dates'}-error`);
    const input = form.elements.namedItem(field);
    if (input) input.setAttribute('aria-invalid', 'true');
    if (target) { target.textContent = text; target.hidden = false; }
  };
  const showFormError = (text) => { const element = $('#backtest-form-error'); element.textContent = text; element.hidden = false; };
  const mapServerErrors = (error) => {
    const details = error.payload?.error?.details;
    if (!Array.isArray(details)) return;
    details.forEach((detail) => setFieldError(detail.field || (Array.isArray(detail.loc) ? detail.loc.at(-1) : ''), detail.reason || detail.msg || error.message));
  };
  const setAvailability = () => { submit.disabled = isSubmitting || !hasAvailableBatch; };
  async function loadBatches() {
    $('#batch-state').className = 'state loading';
    try {
      const payload = await request('/api/v1/data/batches?page=1&page_size=50');
      const items = (payload.data?.items || []).filter((item) => ['AVAILABLE', 'WARNING_AVAILABLE'].includes(item.quality_status));
      const body = $('#batch-table tbody');
      body.replaceChildren();
      items.forEach((item) => {
        const row = document.createElement('tr');
        row.innerHTML = `<td><code>${esc(item.batch_id)}</code></td><td>${esc(item.data_date || '--')}</td><td>${esc(item.quality_status || '--')}</td><td>${esc(item.version || '--')}</td>`;
        row.tabIndex = 0;
        row.setAttribute('aria-label', `选择数据批次 ${item.batch_id}`);
        const choose = () => { $('#data-batch-id').value = item.batch_id || ''; clearFieldErrors(); state('已选择通过质量门禁的数据批次。', 'success', payload.request_id); };
        row.addEventListener('click', choose);
        row.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); choose(); } });
        body.append(row);
      });
      hasAvailableBatch = items.length > 0;
      $('#batch-state').className = `state ${items.length ? 'success' : 'unavailable'}`;
      $('#batch-state').textContent = items.length ? `已加载 ${items.length} 个通过质量门禁的数据批次。选择一行以填入表单。` : '暂无通过质量门禁的数据批次。请先导入并修复数据质量问题。';
      state(items.length ? '请选择已通过质量门禁的数据批次并填写回测区间。' : '回测不可用：缺少通过质量门禁的数据批次。', items.length ? 'success' : 'unavailable', payload.request_id);
    } catch (error) {
      hasAvailableBatch = false;
      $('#batch-state').className = 'state error';
      $('#batch-state').textContent = error.message || '无法加载数据批次。';
      state(error.message || '回测前置数据暂不可用。', error.status === 403 ? 'permission' : 'error', error.payload?.request_id);
    }
    setAvailability();
  }
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (isSubmitting || !hasAvailableBatch) return;
    clearFieldErrors();
    const values = Object.fromEntries(new FormData(form).entries());
    const dates = ['start_date', 'train_end', 'valid_end', 'oos_start', 'end_date'];
    if (dates.some((field) => !values[field])) {
      setFieldError('dates', '请完整填写日期区间。'); showFormError('请检查标记的日期字段后重试。'); return;
    }
    if (!(values.start_date <= values.train_end && values.train_end < values.valid_end && values.valid_end < values.oos_start && values.oos_start <= values.end_date)) {
      setFieldError('dates', '日期必须满足：开始 ≤ 训练 < 验证 < 样本外开始 ≤ 结束。'); showFormError('日期分段无效，未提交回测。'); return;
    }
    if (!/^\d+(\.\d{1,2})?$/.test(values.initial_equity)) {
      setFieldError('initial_equity', '初始权益应为非负金额，最多两位小数。'); showFormError('初始权益格式无效，未提交回测。'); return;
    }
    isSubmitting = true; setAvailability(); submit.textContent = '正在创建回测…';
    state('正在创建回测；服务端会校验版本、质量门禁与研究前置条件。', 'loading');
    try {
      const payload = await request('/api/v1/backtests', { method: 'POST', body: values });
      const runId = payload.data?.run_id;
      $('#backtest-result-panel').hidden = false;
      window.ResearchApp.renderState($('#backtest-result'), 'success', `回测已创建：${runId || '服务端已受理'}。结果是否可用以运行详情为准。`, payload.request_id);
      $('#backtest-links').innerHTML = runId ? `<a class="button-link" href="/backtests/${encodeURIComponent(runId)}/view">查看回测运行</a>` : '<a class="button-link" href="/backtests">查看回测运行</a>';
      state('回测创建请求已成功受理。', 'success', payload.request_id);
      isSubmitting = false; setAvailability(); submit.textContent = submit.dataset.submitLabel;
    } catch (error) {
      mapServerErrors(error);
      showFormError(error.message || '回测无法创建；请检查前置版本和数据质量。');
      state(error.message || '回测无法创建；请检查前置版本和数据质量。', error.status === 403 ? 'permission' : (error.status === 503 ? 'unavailable' : 'error'), error.payload?.request_id);
      isSubmitting = false; setAvailability(); submit.textContent = submit.dataset.submitLabel;
    }
  });
  submit.disabled = true;
  loadBatches();
}());
