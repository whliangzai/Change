(function () {
  'use strict';

  const root = document.querySelector('[data-page="strategies"]');
  if (!root) return;

  const $ = (selector) => root.querySelector(selector);
  const request = async (url, options) =>
    window.ResearchApp.readJson(await window.ResearchApp.apiFetch(url, options));
  let currentRecord = null;
  let createKey = null;
  let reviewKey = null;

  function text(value) {
    return value === null || value === undefined || value === '' ? '--' : String(value);
  }

  function setState(selector, message, kind, requestId) {
    window.ResearchApp.renderState($(selector), kind || '', message, requestId);
  }

  function setHidden(selector, hidden) {
    $(selector).hidden = hidden;
  }

  function recovery(error) {
    if (error.status === 401) {
      return { kind: 'permission', text: '无权限：登录已失效。恢复路径：重新登录后再查询或提交。' };
    }
    if (error.status === 403) {
      return { kind: 'permission', text: '无权限：当前角色不能执行该操作。恢复路径：使用对应角色登录；服务端 RBAC 为最终约束。' };
    }
    if (error.status === 404) {
      return { kind: 'unavailable', text: '不可用原因：该版本不存在或当前账户无权读取。恢复路径：核对服务端返回的版本 ID，或联系管理员确认权限。' };
    }
    if (error.status === 409) {
      return { kind: 'unavailable', text: '不可用原因：版本已发布、已归档或当前状态不允许该决定。恢复路径：刷新当前版本，核对状态后选择允许的审核操作。' };
    }
    return {
      kind: 'error',
      text: `${window.ResearchApp.errorMessage(error.status, error.payload)} 恢复路径：检查字段后重试。`,
    };
  }

  function showRecovery(error, stateSelector) {
    const next = recovery(error);
    if (next.kind === 'permission') {
      $('#strategy-permission').textContent = next.text;
      setHidden('#strategy-permission', false);
    } else if (next.kind === 'unavailable') {
      $('#strategy-unavailable').textContent = next.text;
      setHidden('#strategy-unavailable', false);
    }
    setState(stateSelector, next.text, next.kind === 'error' ? 'error' : next.kind, error.payload?.request_id);
    return next;
  }

  function summaryEntry(label, value, container) {
    const term = document.createElement('dt');
    term.textContent = label;
    const definition = document.createElement('dd');
    definition.textContent = text(value);
    container.append(term, definition);
  }

  function renderRecord(record, diff) {
    const summary = $('#strategy-summary');
    summary.replaceChildren();
    const current = record || {};
    summaryEntry('版本 ID', current.strategy_version_id || $('#strategy-id').value.trim(), summary);
    summaryEntry('状态', current.status || '差异接口未返回状态', summary);
    summaryEntry('版本号', current.version, summary);
    summaryEntry('策略名称', current.name, summary);
    summaryEntry('变更理由', current.change_reason || '差异接口未返回变更理由', summary);
    const parameters = diff?.changes ?? current.parameters ?? {};
    $('#strategy-diff').textContent = JSON.stringify(parameters, null, 2);
    $('#strategy-review-evidence').textContent = `当前服务端证据：版本 ${text(current.strategy_version_id || $('#strategy-id').value.trim())}；状态 ${text(current.status || '未由差异接口返回')}；变更理由 ${text(current.change_reason || '未由差异接口返回')}。请先人工核对参数与差异。`;
  }

  async function loadDiff(id) {
    if (!id) {
      setState('#strategy-state', '请填写策略版本 ID。', 'error');
      return;
    }
    setHidden('#strategy-unavailable', true);
    setHidden('#strategy-permission', true);
    setState('#strategy-state', '正在加载版本差异，已保留现有证据。', 'loading');
    try {
      const payload = await request(`/api/v1/strategies/${encodeURIComponent(id)}/diff`);
      const diff = payload.data || {};
      if (!currentRecord || currentRecord.strategy_version_id !== id) {
        currentRecord = { strategy_version_id: diff.strategy_version_id || id, parameters: diff.changes };
      }
      renderRecord(currentRecord, diff);
      $('#strategy-request-id').textContent = payload.request_id ? `请求号：${payload.request_id}` : '服务端未提供请求号。';
      setState('#strategy-state', '已加载服务端版本差异。', 'success', payload.request_id);
    } catch (error) {
      showRecovery(error, '#strategy-state');
    }
  }

  function resetSubmit(button, label) {
    button.disabled = false;
    button.textContent = label;
  }

  $('#strategy-lookup').addEventListener('submit', (event) => {
    event.preventDefault();
    loadDiff($('#strategy-id').value.trim());
  });
  $('#strategy-refresh').addEventListener('click', () => loadDiff($('#strategy-id').value.trim()));

  $('#strategy-create').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = $('#strategy-create-submit');
    let parameters;
    try {
      parameters = JSON.parse(form.parameters.value || '{}');
    } catch (_) {
      setState('#strategy-create-state', '参数必须是有效 JSON。恢复路径：修正 JSON 结构后重试。', 'error');
      return;
    }
    if (submit.disabled) return;
    createKey = createKey || window.ResearchApp.idempotency();
    submit.disabled = true;
    submit.textContent = '正在提交，不能重复提交';
    setState('#strategy-create-state', '正在向服务端创建策略草稿；请勿重复提交。', 'loading');
    try {
      const payload = await request('/api/v1/strategies', {
        method: 'POST',
        headers: { 'Idempotency-Key': createKey },
        body: {
          name: form.name.value.trim(),
          change_reason: form.change_reason.value.trim(),
          parameters,
        },
      });
      currentRecord = payload.data || null;
      const id = currentRecord?.strategy_version_id;
      if (id) $('#strategy-id').value = id;
      renderRecord(currentRecord);
      setState('#strategy-create-state', `服务端已受理：已创建草稿 ${text(id)}，当前状态为 ${text(currentRecord?.status)}。`, 'success', payload.request_id);
      createKey = null;
      resetSubmit(submit, '创建草稿');
      if (id) await loadDiff(id);
    } catch (error) {
      showRecovery(error, '#strategy-create-state');
      createKey = null;
      resetSubmit(submit, '创建草稿');
    }
  });

  $('#strategy-review').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const id = $('#strategy-id').value.trim();
    const note = form.review_note.value.trim();
    const submit = $('#strategy-review-submit');
    if (!id) {
      setState('#strategy-review-state', '请先填写服务端返回的策略版本 ID。', 'error');
      return;
    }
    if (!note) {
      setState('#strategy-review-state', '审核备注为必填项，请记录人工审核依据后再提交。', 'error');
      return;
    }
    if (submit.disabled) return;
    reviewKey = reviewKey || window.ResearchApp.idempotency();
    submit.disabled = true;
    submit.textContent = '正在提交，不能重复提交';
    setState('#strategy-review-state', '正在向服务端提交审核决定；请勿重复提交。', 'loading');
    try {
      const payload = await request(`/api/v1/strategies/${encodeURIComponent(id)}/submit-review`, {
        method: 'POST',
        headers: { 'Idempotency-Key': reviewKey },
        body: { decision: form.decision.value, review_note: note },
      });
      currentRecord = payload.data || currentRecord;
      renderRecord(currentRecord);
      setState('#strategy-review-state', `服务端已受理审核决定，当前状态为 ${text(currentRecord?.status)}。`, 'success', payload.request_id);
      setState('#strategy-review-result', `真实服务端结果：${text(currentRecord?.strategy_version_id)} 已变更为 ${text(currentRecord?.status)}；仅记录版本状态，不代表交易执行。`, 'success', payload.request_id);
      reviewKey = null;
      resetSubmit(submit, '提交审核决定');
      await loadDiff(id);
    } catch (error) {
      showRecovery(error, '#strategy-review-state');
      reviewKey = null;
      resetSubmit(submit, '提交审核决定');
    }
  });

  loadDiff($('#strategy-id').value.trim());
}());
