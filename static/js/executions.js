(function () {
  'use strict';
  const root = document.querySelector('[data-page="executions"]');
  if (!root) return;
  const $ = (selector) => root.querySelector(selector);
  const request = async (url, options) => window.ResearchApp.readJson(await window.ResearchApp.apiFetch(url, options));
  const state = (text, type, requestId) => window.ResearchApp.renderState($('#execution-state'), type || '', text, requestId);
  const message = (error) => window.ResearchApp.errorMessage(error.status, error.payload);
  const money = /^\d+(\.\d{1,2})?$/;
  let plans = new Map(); let submitKey = null; let isSubmitting = false;

  function setFieldError(field, text) {
    const input = $('#execution-form').elements.namedItem(field);
    const id = { plan_id: 'plan', executed_at: 'executed-at', quantity: 'quantity', unfilled_quantity: 'unfilled-quantity', price: 'price', commission: 'cost', stamp_tax: 'cost', transfer_fee: 'cost', other_fee: 'cost', note: 'note' }[field];
    if (input) input.setAttribute('aria-invalid', 'true');
    const target = id && $(`#execution-${id}-error`);
    if (target) { target.textContent = text; target.hidden = false; }
  }

  function clearFieldErrors() {
    root.querySelectorAll('.field-error').forEach((element) => { element.textContent = ''; element.hidden = true; });
    $('#execution-form').querySelectorAll('[aria-invalid="true"]').forEach((element) => element.removeAttribute('aria-invalid'));
    $('#execution-form-error').hidden = true;
  }

  function showFormError(text) { const target = $('#execution-form-error'); target.textContent = text; target.hidden = false; }

  function mapServerErrors(error) {
    const details = error.payload?.error?.details;
    if (!Array.isArray(details)) return;
    details.forEach((detail) => setFieldError(detail.field || (Array.isArray(detail.loc) ? detail.loc.at(-1) : ''), detail.reason || detail.msg || error.message));
  }

  function renderPlanContext(plan) {
    const target = $('#execution-plan-context');
    if (!plan) { target.className = 'state unavailable'; target.textContent = '请选择一条已确认计划；计划、数量与剩余数量会在提交时再次由服务端核验。'; return; }
    target.className = 'state success';
    target.textContent = `已选择计划 ${plan.plan_no || plan.plan_id}：${plan.side || '--'}，计划数量 ${plan.quantity ?? '--'}，参考价 ${plan.reference_price || plan.reference_low || '--'}，当前状态 ${plan.status || '--'}。仅能录入已发生的人工成交。`;
  }

  function isoWithTimezone(value) {
    if (!value) return '';
    return /[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value.length === 16 ? `${value}:00` : value}+08:00`;
  }

  function availability() { $('#execution-submit').disabled = isSubmitting || !$('#execution-plan-id').value; }

  async function loadPlans(event) {
    if (event) event.preventDefault();
    const date = $('#execution-plan-filter [name="execution_date"]').value;
    const select = $('#execution-plan-id'); plans = new Map(); select.replaceChildren(new Option('正在加载计划…', ''));
    window.ResearchApp.renderState($('#execution-plans-state'), 'loading', '正在加载已确认计划…'); renderPlanContext(); availability();
    if (!date) { window.ResearchApp.renderState($('#execution-plans-state'), 'error', '请选择计划执行日期。'); return; }
    try {
      const payload = await request(`/api/v1/order-plans?execution_date=${encodeURIComponent(date)}&page=1&page_size=200`);
      const items = (payload.data?.items || []).filter((plan) => ['CONFIRMED', 'PARTIALLY_FILLED'].includes(plan.status));
      select.replaceChildren(new Option(items.length ? '请选择已确认计划' : '没有可录入的已确认计划', ''));
      items.forEach((plan) => { plans.set(plan.plan_id, plan); select.append(new Option(`${plan.plan_no || plan.plan_id} | ${plan.side || '--'} | 数量 ${plan.quantity ?? '--'} | ${plan.status}`, plan.plan_id)); });
      window.ResearchApp.renderState($('#execution-plans-state'), items.length ? 'success' : 'unavailable', items.length ? `已加载 ${items.length} 条可录入计划。` : '没有可录入的已确认计划；请先由审核角色确认计划。', payload.request_id);
    } catch (error) {
      window.ResearchApp.renderState($('#execution-plans-state'), error.status === 403 ? 'permission' : (error.status === 503 ? 'unavailable' : 'error'), message(error), error.payload?.request_id);
    }
    availability();
  }

  $('#execution-plan-filter').addEventListener('submit', loadPlans);
  $('#execution-plan-id').addEventListener('change', (event) => { clearFieldErrors(); renderPlanContext(plans.get(event.target.value)); availability(); });
  $('#execution-form').addEventListener('submit', async (event) => {
    event.preventDefault(); if (isSubmitting) return;
    clearFieldErrors(); const form = event.currentTarget; const values = Object.fromEntries(new FormData(form).entries());
    const moneyFields = ['price', 'commission', 'stamp_tax', 'transfer_fee', 'other_fee'];
    if (!values.plan_id) { setFieldError('plan_id', '请选择已确认计划。'); showFormError('未提交：需要选择一条已确认计划。'); return; }
    if (!values.executed_at) { setFieldError('executed_at', '请填写实际成交时间。'); showFormError('未提交：成交时间为必填项。'); return; }
    if (!values.note.trim()) { setFieldError('note', '请说明成交回报来源或部分成交原因。'); showFormError('未提交：人工备注为必填项。'); return; }
    if (!/^\d+$/.test(values.quantity) || Number(values.quantity) < 1) { setFieldError('quantity', '成交数量必须是大于零的整数。'); showFormError('未提交：请检查成交数量。'); return; }
    if (!/^\d+$/.test(values.unfilled_quantity)) { setFieldError('unfilled_quantity', '未成交数量必须是非负整数。'); showFormError('未提交：请检查未成交数量。'); return; }
    if (moneyFields.some((key) => !money.test(values[key]))) { setFieldError('price', '成交价格和费用必须是非负金额，最多两位小数。'); showFormError('未提交：请检查成交价格和费用。'); return; }
    isSubmitting = true; availability(); const submit = $('#execution-submit'); submit.textContent = '正在记录人工成交…'; submitKey = submitKey || window.ResearchApp.idempotency();
    state('正在记录已经发生的人工成交；服务端会核验计划状态、数量和幂等键。', 'loading');
    const body = { ...values, quantity: Number(values.quantity), unfilled_quantity: Number(values.unfilled_quantity), executed_at: isoWithTimezone(values.executed_at), note: values.note.trim(), execution_type: 'MANUAL_ENTRY' };
    try {
      const payload = await request(root.dataset.apiUrl, { method: 'POST', headers: { 'Idempotency-Key': submitKey }, body });
      const record = payload.data || {}; const status = record.status || '服务端已受理';
      state(`人工成交已记录：${record.execution_no || record.execution_id || '服务端已受理'}；计划 ${record.plan_id || values.plan_id}；结果 ${status}${status === 'PARTIALLY_FILLED' ? '（部分成交，剩余数量以服务端记录为准）' : ''}。`, 'success', payload.request_id);
      form.reset(); $('#execution-plan-id').value = ''; renderPlanContext(); submitKey = null;
    } catch (error) {
      mapServerErrors(error); showFormError(message(error)); state(message(error), error.status === 403 ? 'permission' : (error.status === 503 ? 'unavailable' : 'error'), error.payload?.request_id);
    }
    isSubmitting = false; availability(); submit.textContent = submit.dataset.submitLabel;
  });
  $('#execution-form [name="executed_at"]').value = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  availability(); loadPlans();
}());
