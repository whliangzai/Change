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
  let activeVersionRequest = 0;
  let activeListRequest = 0;
  let writing = false;
  const statuses = { DRAFT: '草稿', PENDING_REVIEW: '待审核', PUBLISHED: '已发布', ARCHIVED: '已归档' };

  // Token roles only guide the UI; the server remains the authorization boundary.
  function hasRole(...allowed) {
    try {
      const part = window.ResearchApp.getToken().split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
      const roles = JSON.parse(atob(part.padEnd(Math.ceil(part.length / 4) * 4, '='))).roles || [];
      return roles.some((role) => allowed.includes(role));
    } catch (_) { return false; }
  }
  const canReview = () => hasRole('REVIEWER', 'ADMIN');

  function syncReview() {
    const bound = currentRecord?.strategy_version_id === $('#strategy-id').value.trim();
    const allowed = bound && canReview() && !writing
      ? ({ DRAFT: ['SUBMIT'], PENDING_REVIEW: ['PUBLISH', 'REJECT'] }[currentRecord.status] || []) : [];
    const decision = $('#strategy-review').elements.decision;
    Array.from(decision.options).forEach((option) => { option.disabled = !allowed.includes(option.value); });
    if (!allowed.includes(decision.value)) decision.value = allowed[0] || '';
    decision.disabled = !allowed.length;
    $('#strategy-review-submit').disabled = !allowed.length;
    $('#strategy-review').elements.review_note.disabled = !allowed.length;
    $('#strategy-id').disabled = writing;
    $('#strategy-refresh').disabled = writing;
    $('#strategy-lookup button').disabled = writing;
    $('#strategy-create-submit').disabled = writing || !hasRole('USER', 'ADMIN');
    $('#strategy-review-evidence').textContent = !bound ? '请先加载版本详情，再核对参数与差异。'
      : !canReview() ? '当前账户可查看版本；提交审核、发布和退回需要审核员或管理员权限。'
        : `操作版本：${currentRecord.strategy_version_id} · ${statuses[currentRecord.status] || currentRecord.status}。${allowed.length ? '请核对参数并填写审核依据。' : '当前状态不允许审核操作。'}`;
  }

  function clearVersion() {
    activeVersionRequest += 1;
    currentRecord = null;
    $('#strategy-detail').hidden = true;
    $('#strategy-summary').replaceChildren();
    $('#strategy-diff').textContent = '';
    $('#strategy-review-result').hidden = true;
    $('#strategy-review-state').hidden = true;
    $('#strategy-review').elements.review_note.value = '';
    syncReview();
  }

  async function loadVersions() {
    const listNumber = ++activeListRequest;
    $('#strategy-options').replaceChildren();
    let count = 0;
    try {
      for (let page = 1; ; page += 1) {
        const payload = await request(`/api/v1/strategies?page=${page}&page_size=200`);
        if (listNumber !== activeListRequest) return;
        const data = payload.data || {};
        const items = data.items || [];
        items.forEach((record) => {
          const option = document.createElement('option');
          option.value = record.strategy_version_id;
          option.label = `${record.name || record.code || '策略'} · ${record.version || '--'} · ${statuses[record.status] || record.status}`;
          $('#strategy-options').append(option);
        });
        count += items.length;
        if (!items.length || count >= Number(data.total || 0)) break;
      }
      $('#strategy-id-hint').textContent = count ? `已加载 ${count} 个可访问版本，可按名称或 ID 选择。` : '暂无策略版本，请先创建草稿。';
      if (!count) $('#strategy-create-panel').open = true;
      if (!currentRecord) setState('#strategy-state', count ? '请选择版本并查看详情。' : '暂无策略版本，可从创建草稿开始。', 'empty');
    } catch (error) {
      if (listNumber !== activeListRequest) return;
      $('#strategy-id-hint').textContent = '版本列表加载失败，可刷新重试或输入已知版本 ID。';
      showRecovery(error, '#strategy-state');
    }
  }

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
      const alert = $('#strategy-unavailable');
      const message = alert.querySelector('span');
      if (message) message.textContent = next.text;
      else alert.textContent = next.text;
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
    summaryEntry('状态', statuses[current.status] || current.status || '未提供', summary);
    summaryEntry('版本号', current.version, summary);
    summaryEntry('策略名称', current.name, summary);
    summaryEntry('变更理由', current.change_reason || '差异接口未返回变更理由', summary);
    $('#strategy-evidence-id').textContent = text(current.strategy_version_id || $('#strategy-id').value.trim());
    $('#strategy-evidence-status').textContent = statuses[current.status] || text(current.status || '等待服务端返回');
    $('#strategy-evidence-reason').textContent = text(current.change_reason);
    const parameters = diff?.changes ?? current.parameters ?? {};
    $('#strategy-diff').textContent = JSON.stringify(parameters, null, 2);
    $('#strategy-diff-context').textContent = diff?.base_version ? `对比基准：${diff.base_version}` : '当前版本参数；服务端未指定对比基准。';
    $('#strategy-detail').hidden = false;
    $('#strategy-review-evidence').textContent = `当前服务端证据：版本 ${text(current.strategy_version_id || $('#strategy-id').value.trim())}；状态 ${text(current.status || '未由差异接口返回')}；变更理由 ${text(current.change_reason || '未由差异接口返回')}。请先人工核对参数与差异。`;
  }

  async function loadDiff(id) {
    if (writing) return;
    clearVersion();
    const requestNumber = activeVersionRequest;
    if (!id) {
      setState('#strategy-state', '请填写策略版本 ID。', 'error');
      return;
    }
    setHidden('#strategy-unavailable', true);
    setHidden('#strategy-permission', true);
    setState('#strategy-state', '正在加载版本详情与参数…', 'loading');
    try {
      const payload = await request(`/api/v1/strategies/${encodeURIComponent(id)}/diff`);
      if (requestNumber !== activeVersionRequest || id !== $('#strategy-id').value.trim()) return;
      const diff = payload.data || {};
      if (diff.strategy_version_id !== id) throw new Error('返回版本与所选版本不一致，请刷新后重试。');
      currentRecord = diff;
      renderRecord(currentRecord, diff);
      syncReview();
      setState('#strategy-state', '已加载服务端版本差异。', 'success', payload.request_id);
    } catch (error) {
      if (requestNumber !== activeVersionRequest) return;
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
  $('#strategy-id').addEventListener('input', () => {
    clearVersion();
    setHidden('#strategy-unavailable', true);
    setHidden('#strategy-permission', true);
    setState('#strategy-state', '选择已变更，请重新加载版本详情。', 'empty');
  });
  $('#strategy-refresh').addEventListener('click', async () => {
    clearVersion();
    await loadVersions();
    if ($('#strategy-id').value.trim()) await loadDiff($('#strategy-id').value.trim());
  });

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
    if (submit.disabled || writing) return;
    if (!parameters || Array.isArray(parameters) || typeof parameters !== 'object') {
      setState('#strategy-create-state', '参数必须是 JSON 对象，例如 {}。', 'error');
      return;
    }
    clearVersion();
    writing = true;
    syncReview();
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
      writing = false;
      if (id) await loadDiff(id);
      await loadVersions();
    } catch (error) {
      showRecovery(error, '#strategy-create-state');
      createKey = null;
      resetSubmit(submit, '创建草稿');
    } finally {
      writing = false;
      syncReview();
    }
  });

  $('#strategy-review').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const id = currentRecord?.strategy_version_id;
    const note = form.review_note.value.trim();
    const submit = $('#strategy-review-submit');
    syncReview();
    if (!id || id !== $('#strategy-id').value.trim()) {
      setState('#strategy-review-state', '请先加载并核对当前版本详情。', 'error');
      return;
    }
    if (!note) {
      setState('#strategy-review-state', '审核备注为必填项，请记录人工审核依据后再提交。', 'error');
      return;
    }
    if (submit.disabled) return;
    const decision = form.decision.value;
    writing = true;
    syncReview();
    reviewKey = reviewKey || window.ResearchApp.idempotency();
    submit.disabled = true;
    submit.textContent = '正在提交，不能重复提交';
    setState('#strategy-review-state', '正在向服务端提交审核决定；请勿重复提交。', 'loading');
    try {
      const payload = await request(`/api/v1/strategies/${encodeURIComponent(id)}/submit-review`, {
        method: 'POST',
        headers: { 'Idempotency-Key': reviewKey },
        body: { decision, review_note: note },
      });
      currentRecord = payload.data || currentRecord;
      renderRecord(currentRecord);
      setState('#strategy-review-state', `服务端已受理审核决定，当前状态为 ${text(currentRecord?.status)}。`, 'success', payload.request_id);
      setState('#strategy-review-result', `真实服务端结果：${text(currentRecord?.strategy_version_id)} 已变更为 ${text(currentRecord?.status)}；仅记录版本状态，不代表交易执行。`, 'success', payload.request_id);
      reviewKey = null;
      resetSubmit(submit, '提交审核决定');
      writing = false;
      await loadDiff(id);
      if (currentRecord?.strategy_version_id === id) {
        setState('#strategy-review-result', `服务端已受理：版本 ${id} 的当前状态为 ${statuses[currentRecord.status] || currentRecord.status}。`, 'success', payload.request_id);
      }
      await loadVersions();
    } catch (error) {
      clearVersion();
      showRecovery(error, '#strategy-review-state');
      reviewKey = null;
      resetSubmit(submit, '提交审核决定');
    } finally {
      writing = false;
      syncReview();
    }
  });

  syncReview();
  loadVersions();
}());
