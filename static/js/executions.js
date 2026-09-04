(function () {
  'use strict';
  const root = document.querySelector('[data-page="executions"]');
  if (!root) return;
  const $ = (selector) => root.querySelector(selector);
  const message = (error) => window.ResearchApp.errorMessage(error.status, error.payload);
  const request = async (url, options) => window.ResearchApp.readJson(await window.ResearchApp.apiFetch(url, options));
  const state = (text, type, requestId) => window.ResearchApp.renderState($('#execution-state'), type || '', text, requestId);
  const money = /^\d+(\.\d{1,2})?$/;
  let submitKey = null;

  function isoWithTimezone(value) {
    if (!value) return '';
    return /[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value.length === 16 ? `${value}:00` : value}+08:00`;
  }

  async function loadPlans(event) {
    if (event) event.preventDefault();
    const date = $('#execution-plan-filter [name="execution_date"]').value;
    const select = $('#execution-plan-id'); select.replaceChildren();
    select.append(new Option('正在加载计划…', ''));
    $('#execution-plans-state').textContent = '正在加载已确认计划…';
    try {
      const payload = await request(`/api/v1/order-plans?execution_date=${encodeURIComponent(date)}&page=1&page_size=200`);
      const plans = (payload.data?.items || []).filter((plan) => ['CONFIRMED', 'PARTIALLY_FILLED'].includes(plan.status));
      select.replaceChildren(); select.append(new Option(plans.length ? '请选择计划' : '没有可录入的已确认计划', ''));
      plans.forEach((plan) => select.append(new Option(`${plan.plan_no || plan.plan_id} | ${plan.side} | 剩余以服务端校验为准`, plan.plan_id)));
      $('#execution-plans-state').textContent = plans.length ? `已加载 ${plans.length} 条已确认计划。` : '没有可录入的已确认计划；请先由审核角色确认计划。';
    } catch (error) { $('#execution-plans-state').textContent = message(error); $('#execution-plans-state').className = 'state error'; }
  }

  $('#execution-plan-filter').addEventListener('submit', loadPlans);
  $('#execution-form').addEventListener('submit', async (event) => {
    event.preventDefault(); const form = event.currentTarget; const values = Object.fromEntries(new FormData(form).entries());
    const moneyFields = ['price', 'commission', 'stamp_tax', 'transfer_fee', 'other_fee'];
    if (!values.plan_id || !values.executed_at || !values.note.trim()) return state('请选择计划并完整填写成交时间和人工备注。', 'error');
    if (!/^\d+$/.test(values.quantity) || Number(values.quantity) < 1 || !/^\d+$/.test(values.unfilled_quantity)) return state('成交数量和未成交数量必须为非负整数，成交数量至少为 1。', 'error');
    if (moneyFields.some((key) => !money.test(values[key]))) return state('成交价格和费用必须为非负金额，最多两位小数。', 'error');
    const submit = $('#execution-submit'); submit.disabled = true; submitKey = submitKey || window.ResearchApp.idempotency(); state('正在记录人工成交…', 'loading');
    const body = { ...values, quantity: Number(values.quantity), unfilled_quantity: Number(values.unfilled_quantity), executed_at: isoWithTimezone(values.executed_at), note: values.note.trim(), execution_type: 'MANUAL_ENTRY' };
    try {
      const payload = await request(root.dataset.apiUrl, { method: 'POST', headers: { 'Idempotency-Key': submitKey }, body });
      const record = payload.data || {}; state(`人工成交已记录：${record.execution_no || record.execution_id || '服务端已接受'}；状态 ${record.status || '未知'}。`, 'success', payload.request_id); form.reset(); submitKey = null; submit.disabled = false;
    } catch (error) { state(message(error), 'error', error.payload?.request_id); submit.disabled = false; }
  });
  $('#execution-form [name="executed_at"]').value = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  loadPlans();
}());
