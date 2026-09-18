(function () {
  'use strict';
  const root = document.querySelector('[data-page="executions"]');
  if (!root) return;
  const $ = (selector) => root.querySelector(selector);
  const request = async (url, options) => window.ResearchApp.readJson(await window.ResearchApp.apiFetch(url, options));
  const state = (text, type, requestId) => window.ResearchApp.renderState($('#execution-state'), type || '', text, requestId);
  const message = (error) => window.ResearchApp.errorMessage(error.status, error.payload);
  const statusLabel = (value, domain) => window.ResearchApp.statusLabel ? window.ResearchApp.statusLabel(value, domain) : String(value ?? '--');
  const money = /^\d+(\.\d{1,2})?$/;
  let plans = new Map(); let submitKey = null; let isSubmitting = false; let confirming = false; let pendingValues = null;

  function setFieldError(field, text) {
    const form = $('#execution-form'); const input = form.elements.namedItem(field);
    const id = { plan_id: 'plan', executed_at: 'executed-at', quantity: 'quantity', unfilled_quantity: 'unfilled-quantity', price: 'price', commission: 'commission', stamp_tax: 'stamp-tax', transfer_fee: 'transfer-fee', other_fee: 'other-fee', note: 'note' }[field];
    if (input) input.setAttribute('aria-invalid', 'true'); const target = id && $(`#execution-${id}-error`); if (target) { target.textContent = text; target.hidden = false; }
  }
  function clearFieldErrors() { root.querySelectorAll('.field-error').forEach((element) => { element.textContent = ''; element.hidden = true; }); $('#execution-form').querySelectorAll('[aria-invalid="true"]').forEach((element) => element.removeAttribute('aria-invalid')); $('#execution-form-error').hidden = true; }
  function showFormError(text) { const target = $('#execution-form-error'); target.textContent = text; target.hidden = false; }
  function mapServerErrors(error) { const details = error.payload?.error?.details; if (!Array.isArray(details)) return; details.forEach((detail) => setFieldError(detail.field || (Array.isArray(detail.loc) ? detail.loc.at(-1) : ''), detail.reason || detail.msg || error.message)); }
  function renderPlanContext(plan) {
    const target = $('#execution-plan-context');
    if (!plan) { target.className = 'state unavailable'; target.textContent = '请选择一条已确认计划；计划、数量与剩余数量会在提交时再次由服务端核验。'; return; }
    const remaining = plan.remaining_quantity ?? plan.quantity;
    target.className = 'state success'; target.textContent = `已选择计划 ${plan.plan_no || plan.plan_id}：${plan.side || '--'}，计划剩余数量 ${remaining ?? '--'}，参考价 ${plan.reference_price || plan.reference_low || '--'}，当前状态 ${statusLabel(plan.status, 'plan')}。仅能录入已发生的人工成交。`;
  }
  function isoWithTimezone(value) { if (!value) return ''; return /[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value.length === 16 ? `${value}:00` : value}+08:00`; }
  function availability() { $('#execution-submit').disabled = isSubmitting || !$('#execution-plan-id').value; }
  function appendSummary(values, plan) {
    const target = $('#execution-confirmation-summary'); target.replaceChildren();
    const planQuantity = Number(plan?.remaining_quantity ?? plan?.quantity ?? 0); const total = Number(values.quantity || 0) + Number(values.unfilled_quantity || 0);
    [['对象 / 计划', `${plan?.plan_no || values.plan_id}`], ['成交时间', `${values.executed_at}（UTC+8）`], ['成交 / 未成交数量', `${values.quantity} / ${values.unfilled_quantity}，合计 ${total} / 计划 ${planQuantity}`], ['成交价格', values.price], ['费用', `佣金 ${values.commission}；印花税 ${values.stamp_tax}；过户费 ${values.transfer_fee}；其他费用 ${values.other_fee}`], ['备注', values.note]].forEach(([label, value]) => { const term = document.createElement('dt'); term.textContent = label; const definition = document.createElement('dd'); definition.textContent = String(value ?? '--'); target.append(term, definition); });
  }
  function setConfirmationMode(enabled) {
    confirming = enabled; const form = $('#execution-form'); form.querySelectorAll('input, select, textarea').forEach((element) => { if (element.id !== 'execution-submit' && element.id !== 'execution-confirm') element.disabled = enabled; }); $('#execution-confirmation').hidden = !enabled; $('#execution-submit').textContent = enabled ? '返回修改' : $('#execution-submit').dataset.submitLabel;
  }

  async function loadPlans(event) {
    if (event) event.preventDefault(); const date = $('#execution-plan-filter [name="execution_date"]').value; const select = $('#execution-plan-id'); plans = new Map(); select.replaceChildren(new Option('正在加载计划…', '')); window.ResearchApp.renderState($('#execution-plans-state'), 'loading', '正在加载已确认计划…'); renderPlanContext(); availability();
    if (!date) { window.ResearchApp.renderState($('#execution-plans-state'), 'error', '请选择计划执行日期。'); return; }
    try {
      const payload = await request(`/api/v1/order-plans?execution_date=${encodeURIComponent(date)}&page=1&page_size=200`); const items = (payload.data?.items || []).filter((plan) => ['CONFIRMED', 'PARTIALLY_FILLED'].includes(plan.status)); select.replaceChildren(new Option(items.length ? '请选择已确认计划' : '没有可录入的已确认计划', '')); items.forEach((plan) => { plans.set(plan.plan_id, plan); select.append(new Option(`${plan.plan_no || plan.plan_id} | ${plan.side || '--'} | 数量 ${plan.quantity ?? '--'} | ${statusLabel(plan.status, 'plan')}`, plan.plan_id)); }); window.ResearchApp.renderState($('#execution-plans-state'), items.length ? 'success' : 'unavailable', items.length ? `已加载 ${items.length} 条可录入计划。` : '没有可录入的已确认计划；请先由审核角色确认计划。', payload.request_id);
    } catch (error) { window.ResearchApp.renderState($('#execution-plans-state'), error.status === 403 ? 'permission' : (error.status === 503 ? 'unavailable' : 'error'), message(error), error.payload?.request_id); }
    availability();
  }

  async function submitConfirmed() {
    const values = pendingValues; const submit = $('#execution-confirm'); if (!values || isSubmitting) return; isSubmitting = true; availability(); submit.disabled = true; $('#execution-submit').textContent = '正在记录人工成交…'; state('正在记录已经发生的人工成交；服务端会核验计划状态、数量和幂等键。', 'loading');
    try {
      const payload = await request(root.dataset.apiUrl, { method: 'POST', headers: { 'Idempotency-Key': submitKey }, body: values }); const record = payload.data || {}; const status = record.status || '服务端已受理'; state(`人工成交已记录：${record.execution_no || record.execution_id || '服务端已受理'}；计划 ${record.plan_id || values.plan_id}；结果 ${statusLabel(status, 'execution')}${status === 'PARTIALLY_FILLED' ? '（部分成交，剩余数量以服务端记录为准）' : ''}。`, 'success', payload.request_id); $('#execution-form').reset(); $('#execution-plan-id').value = ''; setConfirmationMode(false); renderPlanContext(); submitKey = null; pendingValues = null;
    } catch (error) { mapServerErrors(error); showFormError(message(error)); state(message(error), error.status === 403 ? 'permission' : (error.status === 503 ? 'unavailable' : 'error'), error.payload?.request_id); submit.disabled = false; }
    isSubmitting = false; availability(); $('#execution-submit').textContent = confirming ? '返回修改' : $('#execution-submit').dataset.submitLabel;
  }

  $('#execution-plan-filter').addEventListener('submit', loadPlans);
  $('#execution-plan-id').addEventListener('change', (event) => { clearFieldErrors(); renderPlanContext(plans.get(event.target.value)); availability(); });
  $('#execution-form').addEventListener('submit', (event) => {
    event.preventDefault(); if (isSubmitting) return; if (confirming) { setConfirmationMode(false); pendingValues = null; state('已返回修改，尚未提交人工成交。', 'unavailable'); $('#execution-submit').focus(); return; }
    clearFieldErrors(); const form = event.currentTarget; const values = Object.fromEntries(new FormData(form).entries()); const plan = plans.get(values.plan_id); const moneyFields = ['price', 'commission', 'stamp_tax', 'transfer_fee', 'other_fee'];
    if (!values.plan_id) { setFieldError('plan_id', '请选择已确认计划。'); showFormError('未提交：需要选择一条已确认计划。'); return; }
    if (!values.executed_at) { setFieldError('executed_at', '请填写实际成交时间。'); showFormError('未提交：成交时间为必填项。'); return; }
    if (!values.note.trim()) { setFieldError('note', '请说明成交回报来源或部分成交原因。'); showFormError('未提交：人工备注为必填项。'); return; }
    if (!/^\d+$/.test(values.quantity) || Number(values.quantity) < 1) { setFieldError('quantity', '成交数量必须是大于零的整数。'); showFormError('未提交：请检查成交数量。'); return; }
    if (!/^\d+$/.test(values.unfilled_quantity)) { setFieldError('unfilled_quantity', '未成交数量必须是非负整数。'); showFormError('未提交：请检查未成交数量。'); return; }
    const remainingQuantity = plan?.remaining_quantity ?? plan?.quantity;
    if (plan && Number(values.quantity) + Number(values.unfilled_quantity) !== Number(remainingQuantity)) { setFieldError('quantity', `成交数量与未成交数量合计应为计划剩余数量 ${remainingQuantity}，最终以服务端校验为准。`); setFieldError('unfilled_quantity', `请与成交数量合计为计划剩余数量 ${remainingQuantity}。`); showFormError('未提交：数量与计划剩余数量不一致。'); return; }
    if (!money.test(values.price) || Number(values.price) <= 0) { setFieldError('price', '成交价格必须是大于 0 的金额，最多两位小数。'); showFormError('未提交：请检查成交价格。'); return; }
    moneyFields.slice(1).forEach((field) => { if (!money.test(values[field])) setFieldError(field, '费用必须是非负金额，最多两位小数。'); });
    if (moneyFields.slice(1).some((field) => !money.test(values[field]))) { showFormError('未提交：请检查费用字段。'); return; }
    values.quantity = Number(values.quantity); values.unfilled_quantity = Number(values.unfilled_quantity); values.executed_at = isoWithTimezone(values.executed_at); values.note = values.note.trim(); values.execution_type = 'MANUAL_ENTRY'; submitKey = submitKey || window.ResearchApp.idempotency(); pendingValues = values; appendSummary(values, plan); setConfirmationMode(true); state('请核对成交对象、数量和费用摘要；点击“确认提交”后才会发送记录请求。', 'warning');
  });
  $('#execution-confirm').addEventListener('click', submitConfirmed);
  $('#execution-form [name="executed_at"]').value = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16); availability(); loadPlans();
}());
