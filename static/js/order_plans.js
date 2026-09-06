(function () {
  'use strict';

  const root = document.querySelector('[data-page="order-plans"]');
  if (!root) return;

  const $ = (selector) => root.querySelector(selector);
  const errorMessage = (error) => window.ResearchApp.errorMessage(error.status, error.payload);
  const request = async (url, options) =>
    window.ResearchApp.readJson(await window.ResearchApp.apiFetch(url, options));
  const planState = (text, kind, requestId) =>
    window.ResearchApp.renderState($('#plan-state'), kind || '', text, requestId);
  let currentPage = 1;
  let activeKey = null;

  function text(value) {
    return value === null || value === undefined || value === '' ? '--' : String(value);
  }

  function setHidden(selector, hidden) {
    $(selector).hidden = hidden;
  }

  function setDecisionState(message, kind, requestId) {
    window.ResearchApp.renderState($('#plan-decision-state'), kind || '', message, requestId);
  }

  function riskState(plan) {
    return String(plan.risk_snapshot?.risk_state || plan.risk_snapshot?.state || '未提供');
  }

  function confirmationBlock(plan) {
    if (plan.status !== 'PENDING_CONFIRMATION') {
      return `不可用原因：计划当前为 ${text(plan.status)}，不再处于待人工确认状态。恢复路径：刷新列表，选择服务端仍为待确认的计划。`;
    }
    if (['STOP_NEW', 'MANUAL_REVIEW'].includes(riskState(plan))) {
      return `不可用原因：风险状态为 ${riskState(plan)}，服务端不允许确认新增计划。恢复路径：记录跳过决定，或待风险状态恢复后刷新计划。`;
    }
    return '';
  }

  function errorRecovery(error) {
    if (error.status === 401) {
      return { kind: 'permission', text: '无权限：登录已失效。恢复路径：重新登录后再次查询；页面不会使用本地缓存放行操作。' };
    }
    if (error.status === 403) {
      return { kind: 'permission', text: '无权限：当前角色不能记录人工决定。恢复路径：使用审核角色登录；服务端 RBAC 为最终约束。' };
    }
    if (error.status === 409) {
      return { kind: 'unavailable', text: '不可用原因：计划已过期、风险状态变化或版本冲突。恢复路径：刷新计划和当前版本，重新核对后再记录人工决定。' };
    }
    return { kind: 'error', text: `${errorMessage(error)} 恢复路径：检查输入或服务状态后重试。` };
  }

  function appendCell(row, value, className) {
    const cell = document.createElement('td');
    if (className) cell.className = className;
    cell.textContent = text(value);
    row.append(cell);
    return cell;
  }

  function renderPager(data) {
    const target = $('#plan-pagination');
    target.replaceChildren();
    const pages = Math.max(1, Math.ceil((data.total || 0) / (data.page_size || 50)));
    const label = document.createElement('span');
    label.textContent = `第 ${data.page || 1}/${pages} 页，共 ${data.total || 0} 条`;
    target.append(label);
    [['上一页', (data.page || 1) - 1], ['下一页', (data.page || 1) + 1]].forEach(([labelText, page]) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = labelText;
      button.disabled = page < 1 || page > pages;
      button.addEventListener('click', () => load(page));
      target.append(button);
    });
  }

  function render(plans) {
    const body = $('#plan-table tbody');
    body.replaceChildren();
    (plans.items || []).forEach((plan) => {
      const row = document.createElement('tr');
      const planNumber = document.createElement('code');
      planNumber.textContent = text(plan.plan_no || plan.plan_id);
      const planCell = document.createElement('td');
      planCell.append(planNumber);
      row.append(planCell);
      appendCell(row, plan.side);
      appendCell(row, plan.security_id || plan.symbol);
      appendCell(row, plan.quantity, 'numeric');
      appendCell(row, `${text(plan.reference_low)} - ${text(plan.reference_high)}`, 'numeric');
      appendCell(row, plan.estimated_cost, 'numeric');
      appendCell(row, riskState(plan));
      appendCell(row, plan.status);
      appendCell(row, plan.version);
      const actionCell = document.createElement('td');
      const action = document.createElement('button');
      const blocked = confirmationBlock(plan);
      action.type = 'button';
      action.textContent = blocked && plan.status !== 'PENDING_CONFIRMATION' ? '当前不可审核' : '人工审核';
      action.disabled = plan.status !== 'PENDING_CONFIRMATION';
      action.title = blocked || '查看服务端返回的计划、风险和版本证据后记录人工决定。';
      action.addEventListener('click', () => selectPlan(plan));
      actionCell.append(action);
      row.append(actionCell);
      body.append(row);
    });
    if (!(plans.items || []).length) {
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 10;
      cell.className = 'muted';
      cell.textContent = '空数据：该日期没有当前账户可见的计划。恢复路径：调整日期，或完成日终计划生成后刷新。';
      row.append(cell);
      body.append(row);
    }
    renderPager(plans);
  }

  function renderEvidence(plan) {
    const evidence = $('#plan-decision-evidence');
    evidence.replaceChildren();
    [
      ['计划对象', plan.plan_no || plan.plan_id],
      ['方向 / 标的', `${text(plan.side)} / ${text(plan.security_id || plan.symbol)}`],
      ['数量', plan.quantity],
      ['模拟参考价', `${text(plan.reference_low)} - ${text(plan.reference_high)}（不是委托价）`],
      ['预估成本', plan.estimated_cost],
      ['风险快照', riskState(plan)],
      ['当前状态 / 版本', `${text(plan.status)} / ${text(plan.version)}`],
      ['到期影响', '确认时由服务端校验计划是否到期；到期计划必须重新生成。'],
    ].forEach(([label, value]) => {
      const term = document.createElement('dt');
      term.textContent = label;
      const definition = document.createElement('dd');
      definition.textContent = text(value);
      evidence.append(term, definition);
    });
  }

  function selectPlan(plan) {
    const form = $('#plan-decision-form');
    const block = confirmationBlock(plan);
    form.plan_id.value = plan.plan_id;
    form.expected_version.value = String(plan.version);
    form.review_note.value = '';
    form.decision.value = block && plan.status === 'PENDING_CONFIRMATION' ? 'SKIP' : 'CONFIRM';
    [...form.decision.options].forEach((option) => {
      option.disabled = option.value === 'CONFIRM' && Boolean(block);
    });
    activeKey = null;
    $('#plan-decision-target').textContent = '以下对象与证据来自本次服务端查询，请在记录决定前人工核对。';
    renderEvidence(plan);
    setHidden('#plan-decision-unavailable', !block);
    if (block) $('#plan-decision-unavailable').textContent = block;
    $('#plan-decision-state').hidden = true;
    form.hidden = false;
    $('#plan-decision-panel').hidden = false;
    form.review_note.focus();
  }

  async function load(page) {
    currentPage = page || 1;
    const date = $('#plan-execution-date').value;
    if (!date) {
      planState('请选择执行日期。', 'error');
      return;
    }
    setHidden('#plan-unavailable', true);
    setHidden('#plan-permission', true);
    planState('正在加载计划，已保留当前表格布局。', 'loading');
    try {
      const payload = await request(`${root.dataset.apiUrl}?execution_date=${encodeURIComponent(date)}&page=${currentPage}&page_size=50`);
      const plans = payload.data || {};
      render(plans);
      if (!(plans.items || []).length) setHidden('#plan-unavailable', false);
      planState(`已加载 ${plans.total || 0} 条可见计划。`, 'success', payload.request_id);
    } catch (error) {
      const recovery = errorRecovery(error);
      if (recovery.kind === 'permission') {
        $('#plan-permission').textContent = recovery.text;
        setHidden('#plan-permission', false);
      } else if (recovery.kind === 'unavailable') {
        $('#plan-unavailable').textContent = recovery.text;
        setHidden('#plan-unavailable', false);
      }
      planState(recovery.text, recovery.kind === 'error' ? 'error' : recovery.kind, error.payload?.request_id);
    }
  }

  $('#plan-filter').addEventListener('submit', (event) => {
    event.preventDefault();
    load(1);
  });
  $('#plan-decision-cancel').addEventListener('click', () => {
    $('#plan-decision-panel').hidden = true;
  });
  $('#plan-decision-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form).entries());
    const submit = $('#plan-decision-submit');
    if (!String(values.review_note || '').trim()) {
      setDecisionState('审核备注为必填项，请记录人工核对依据后再提交。', 'error');
      return;
    }
    if (submit.disabled) return;
    activeKey = activeKey || window.ResearchApp.idempotency();
    submit.disabled = true;
    submit.textContent = '正在提交，不能重复提交';
    setDecisionState('正在向服务端提交人工决定；请勿重复提交。', 'loading');
    try {
      const payload = await request(`${root.dataset.apiUrl}/${encodeURIComponent(values.plan_id)}/confirm`, {
        method: 'POST',
        headers: { 'Idempotency-Key': activeKey },
        body: {
          decision: values.decision,
          review_note: String(values.review_note).trim(),
          expected_version: Number(values.expected_version),
        },
      });
      setDecisionState(`服务端受理：已记录人工决定，当前计划状态为 ${text(payload.data?.status)}。`, 'success', payload.request_id);
      form.hidden = true;
      activeKey = null;
      await load(currentPage);
    } catch (error) {
      const recovery = errorRecovery(error);
      setDecisionState(recovery.text, recovery.kind === 'error' ? 'error' : recovery.kind, error.payload?.request_id);
      if (recovery.kind === 'permission') {
        $('#plan-permission').textContent = recovery.text;
        setHidden('#plan-permission', false);
      }
      submit.disabled = false;
      submit.textContent = '记录人工决定';
      activeKey = null;
    }
  });

  load(1);
}());
