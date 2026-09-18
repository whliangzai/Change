(function () {
  const root = document.querySelector('[data-page="data-import"]');
  if (!root) return;
  const $ = (selector) => root.querySelector(selector);
  const form = $('#data-import-form');
  const submit = $('#data-import-submit');
  let isSubmitting = false;
  let confirming = false;
  let pendingValues = null;
  const fieldIds = {
    file_location: 'file', source_name: 'source', available_at: 'available',
    information_cutoff_at: 'cutoff', license_note: 'license',
  };
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const request = async (url, options) => window.ResearchApp.readJson(await window.ResearchApp.apiFetch(url, options));
  const state = (text, kind, requestId) => window.ResearchApp.renderState($('#data-import-state'), kind || '', text, requestId);
  const statusLabel = (value, domain) => window.ResearchApp.statusLabel ? window.ResearchApp.statusLabel(value, domain) : String(value ?? '--');
  const iso = (value) => value ? new Date(value).toISOString() : value;
  const roles = () => {
    try {
      const token = window.ResearchApp.getToken();
      const raw = token && token.split('.')[1];
      const claims = raw && JSON.parse(atob(raw.replace(/-/g, '+').replace(/_/g, '/').padEnd(raw.length + (4 - raw.length % 4) % 4, '=')));
      return claims && Array.isArray(claims.roles) ? claims.roles : [];
    } catch (_) { return []; }
  };
  const admin = roles().includes('ADMIN');
  const clearFieldErrors = () => {
    root.querySelectorAll('.field-error').forEach((element) => { element.hidden = true; element.textContent = ''; });
    form.querySelectorAll('[aria-invalid="true"]').forEach((element) => element.removeAttribute('aria-invalid'));
    $('#data-import-form-error').hidden = true;
    $('#data-import-form-error').textContent = '';
  };
  const setFieldError = (field, text) => {
    const suffix = fieldIds[field];
    const input = form.elements.namedItem(field);
    const target = suffix && $(`#data-import-${suffix}-error`);
    if (input) input.setAttribute('aria-invalid', 'true');
    if (target) { target.textContent = text; target.hidden = false; }
  };
  const showFormError = (text) => {
    const target = $('#data-import-form-error');
    target.textContent = text;
    target.hidden = false;
  };
  const appendSummary = (target, pairs) => {
    target.replaceChildren();
    pairs.forEach(([label, value]) => { const term = document.createElement('dt'); term.textContent = label; const definition = document.createElement('dd'); definition.textContent = String(value ?? '--'); target.append(term, definition); });
  };
  const setConfirmationMode = (enabled) => {
    confirming = enabled;
    form.querySelectorAll('input, select, textarea').forEach((element) => { if (element.id !== 'data-import-submit' && element.id !== 'data-import-confirm') element.disabled = enabled; });
    $('#data-import-confirmation').hidden = !enabled;
    submit.textContent = enabled ? '返回修改' : submit.dataset.submitLabel;
  };
  const mapServerErrors = (error) => {
    const details = error.payload?.error?.details;
    if (!Array.isArray(details)) return;
    details.forEach((detail) => {
      const field = detail.field || (Array.isArray(detail.loc) ? detail.loc.at(-1) : '');
      if (field) setFieldError(field, detail.reason || detail.msg || error.message);
    });
  };
  const renderIssues = (summary) => {
    const issues = summary && [
      ...(Array.isArray(summary.errors) ? summary.errors : []),
      ...(Array.isArray(summary.issues) ? summary.issues : []),
      ...(Array.isArray(summary.failures) ? summary.failures : []),
      ...(Array.isArray(summary.warnings) ? summary.warnings : []),
    ];
    $('#data-import-issues').innerHTML = (Array.isArray(issues) && issues.length ? issues : ['未发现质量错误或警告。']).map((issue) => `<li>${esc(typeof issue === 'string' ? issue : `${issue.code || '质量规则'}：${issue.message || issue.detail || JSON.stringify(issue)}`)}</li>`).join('');
  };
  const render = (record, requestId, blocked) => {
    const quality = record.quality_summary || {};
    $('#data-import-result').innerHTML = [['批次 ID', record.batch_id], ['质量状态', statusLabel(record.quality_status, 'quality')], ['记录数', record.record_count], ['版本', record.version]].map(([key, value]) => `<dt>${key}</dt><dd><code>${esc(value ?? '--')}</code></dd>`).join('');
    renderIssues(quality);
    const links = $('#data-import-links');
    links.innerHTML = record.batch_id ? `<a class="button-link" href="/data-quality?batch_id=${encodeURIComponent(record.batch_id)}">查看质量明细</a>` : '';
    links.hidden = !record.batch_id;
    state(blocked ? '导入已被数据质量闸门阻断；请处理下方问题后重新导入。' : `导入完成，质量状态：${statusLabel(record.quality_status, 'quality')}。`, blocked ? 'unavailable' : 'success', requestId);
  };

  $('#data-import-permission').textContent = admin ? '当前会话显示管理员权限；服务端仍会在提交时授权与审计。' : '当前会话未显示管理员权限，无法提交导入；服务端也会拒绝无权请求。';
  $('#data-import-permission').className = `state ${admin ? 'success' : 'permission'}`;
  submit.disabled = !admin;
  async function submitLocalImport() {
    const values = pendingValues;
    if (!values || isSubmitting) return;
    isSubmitting = true; submit.disabled = true; $('#data-import-confirm').disabled = true; submit.textContent = '正在导入并校验…'; state('正在导入授权行情并执行质量校验；请勿重复提交。', 'loading');
    try {
      const payload = await request('/api/v1/data/imports', { method: 'POST', body: values }); render(payload.data || {}, payload.request_id, false); setConfirmationMode(false); pendingValues = null;
    } catch (error) {
      const details = error.payload?.error?.details; const blocked = Array.isArray(details) && details.find((detail) => detail.batch_id); mapServerErrors(error);
      if (blocked) render({ batch_id: blocked.batch_id, quality_status: 'UNAVAILABLE', quality_summary: blocked.quality || {} }, error.payload?.request_id, true);
      else { showFormError(error.message || '导入失败；请检查文件、授权信息和服务状态。'); state(error.message || '导入失败；请检查文件、授权信息和服务状态。', error.status === 403 ? 'permission' : 'error', error.payload?.request_id); }
      $('#data-import-confirm').disabled = false;
    }
    isSubmitting = false; submit.disabled = !admin; submit.textContent = confirming ? '返回修改' : submit.dataset.submitLabel;
  }
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (isSubmitting || !admin) return;
    if (confirming) { setConfirmationMode(false); pendingValues = null; state('已返回修改，尚未提交本地导入。', 'unavailable'); submit.focus(); return; }
    clearFieldErrors();
    const values = Object.fromEntries(new FormData(form).entries());
    const required = ['file_location', 'source_name', 'available_at', 'information_cutoff_at', 'license_note'];
    const missing = required.filter((field) => !String(values[field] || '').trim());
    if (missing.length) {
      missing.forEach((field) => setFieldError(field, '此项为必填。'));
      showFormError('请检查标记的必填字段后重试。');
      form.elements.namedItem(missing[0])?.focus();
      state('导入尚未提交：缺少必填信息。', 'error');
      return;
    }
    values.available_at = iso(values.available_at);
    values.information_cutoff_at = iso(values.information_cutoff_at);
    const version = String(values.version || '').trim();
    if (version) values.version = version;
    else delete values.version;
    pendingValues = values;
    appendSummary($('#data-import-confirmation-summary'), [['对象', '本地授权文件导入'], ['文件', values.file_location], ['来源 / 数据类型', `${values.source_name} / ${values.data_type}`], ['可得时间 / 信息截点', `${values.available_at} / ${values.information_cutoff_at}`], ['版本', values.version || '由系统生成内容版本']]);
    setConfirmationMode(true); state('请核对摘要；点击“确认提交”后才会发送本地导入请求。', 'warning');
  });
  $('#data-import-confirm').addEventListener('click', submitLocalImport);

  const tabButtons = [$('#provider-import-tab'), $('#local-import-tab')];
  const tabPanels = [$('#provider-import-panel'), $('#local-import-panel')];
  const selectImportTab = (index, focus = false) => {
    tabButtons.forEach((button, buttonIndex) => {
      const selected = buttonIndex === index;
      button.setAttribute('aria-selected', String(selected)); button.tabIndex = selected ? 0 : -1;
      tabPanels[buttonIndex].hidden = !selected;
    });
    if (focus) tabButtons[index].focus();
  };
  tabButtons.forEach((button, index) => {
    button.addEventListener('click', () => selectImportTab(index));
    button.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabButtons.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabButtons.length) % tabButtons.length;
      selectImportTab(next, true);
    });
  });
  selectImportTab(0);

  const providerForm = $('#provider-import-form');
  if (providerForm) {
    const providerSelect = $('#provider-import-provider');
    const providerDate = $('#provider-import-date');
    const providerScope = $('#provider-import-scope');
    const providerSubmit = $('#provider-import-submit');
    const providerCapabilities = $('#provider-capabilities-state');
    const providerFullReason = $('#provider-full-reason');
    const providerQueue = $('#provider-queue-status');
    const providerState = $('#provider-import-state');
    const providerResult = $('#provider-import-result');
    const providerLinks = $('#provider-import-links');
    const providerRefresh = $('#provider-import-refresh');
    let capabilities = null;
    let providerJobId = null;
    let providerPollTimer = null;
    let providerPollBusy = false;
    let providerPollDeadline = 0;
    let providerSubmitting = false;
    let providerJobActive = false;
    let providerConfirming = false;
    let providerPendingValues = null;

    const providerRenderState = (text, kind, requestId) => window.ResearchApp.renderState(providerState, kind || '', text, requestId);
    const capabilityFor = () => capabilities?.providers?.[providerSelect.value] || capabilities?.[providerSelect.value] || {};
    const queueIsAvailable = () => Boolean(capabilities?.queue_available ?? capabilities?.queue?.available);
    const statusKind = (status) => status === 'SUCCEEDED' ? 'success' : (status === 'FAILED' ? 'error' : (status === 'RUNNING' || status === 'QUEUED' ? 'loading' : 'unavailable'));
    const qualityFrom = (job) => job.quality_status || job.value?.quality_status || '--';
    const setProviderConfirmationMode = (enabled) => {
      providerConfirming = enabled;
      providerForm.querySelectorAll('input, select').forEach((element) => { if (element.id !== 'provider-import-submit' && element.id !== 'provider-import-confirm') element.disabled = enabled; });
      $('#provider-import-confirmation').hidden = !enabled;
      providerSubmit.textContent = enabled ? '返回修改' : '提交供应商导入';
    };

    function updateProviderControls() {
      const capability = capabilityFor();
      const fullOpen = Boolean(capability.full_open ?? capability.full_enabled);
      providerScope.querySelector('option[value="full"]').disabled = !fullOpen;
      if (!fullOpen && providerScope.value === 'full') providerScope.value = 'pilot';
      providerFullReason.textContent = fullOpen ? '' : (capability.full_reason || 'full 未开放；请先完成 pilot 审核并显式启用门禁。');
      providerFullReason.hidden = fullOpen;
      providerQueue.textContent = `队列状态：${queueIsAvailable() ? '可用' : '不可用，请检查 readiness、Redis 和 worker。'}`;
      providerSubmit.disabled = !admin || providerSubmitting || providerJobActive || !capability.enabled || !queueIsAvailable();
    }

    function renderProviderCapabilities(payload, requestId) {
      capabilities = payload.data || {};
      const queueAvailable = queueIsAvailable();
      const enabled = Object.entries(capabilities.providers || {}).filter(([, value]) => value.enabled).map(([name]) => name);
      providerCapabilities.textContent = `供应商状态：${enabled.length ? enabled.join('、') + ' 已启用' : '当前没有启用的供应商'}；队列${queueAvailable ? '可用' : '不可用'}。`;
      providerCapabilities.className = `state ${queueAvailable && enabled.length ? 'success' : 'unavailable'}`;
      updateProviderControls();
      if (!admin) providerRenderState('当前会话不是管理员，服务端将拒绝供应商导入。', 'permission', requestId);
      else if (!queueAvailable) providerRenderState('队列不可用；任务提交会保留失败证据，恢复 Redis/worker 后可从管理页重试。', 'unavailable', requestId);
      else if (!enabled.length) providerRenderState('没有启用的供应商；请由部署人员配置供应商开关和部署凭证。', 'unavailable', requestId);
      else providerRenderState('选择供应商、交易日期和范围后提交 pilot 导入。', '', requestId);
    }

    function renderProviderJob(job, requestId) {
      const status = String(job.status || '').toUpperCase();
      providerJobId = job.job_id || job.id || providerJobId;
      providerResult.innerHTML = [
        ['任务号', providerJobId], ['状态', statusLabel(status, 'job')], ['尝试次数', job.attempt],
        ['运行号', job.run_number], ['批次 ID', job.batch_id], ['质量状态', statusLabel(qualityFrom(job), 'quality')],
        ['阶段', job.phase], ['完成时间', job.completed_at || job.ended_at],
      ].map(([key, value]) => `<dt>${esc(key)}</dt><dd><code>${esc(value ?? '--')}</code></dd>`).join('');
      providerLinks.replaceChildren();
      if (job.batch_id) {
        const qualityLink = document.createElement('a'); qualityLink.className = 'button-link'; qualityLink.href = `/data-quality?batch_id=${encodeURIComponent(job.batch_id)}`; qualityLink.textContent = '查看质量明细'; providerLinks.append(qualityLink);
      }
      if (status === 'FAILED') {
        const retryLink = document.createElement('a'); retryLink.className = 'button-link'; retryLink.href = `/admin?job_id=${encodeURIComponent(providerJobId || '')}`; retryLink.textContent = job.retryable ? '前往管理页重试' : '前往管理页查看'; providerLinks.append(retryLink);
      }
      if (status === 'QUEUED' && job.requeueable) {
        const recoverLink = document.createElement('a'); recoverLink.className = 'button-link'; recoverLink.href = `/admin?job_id=${encodeURIComponent(providerJobId || '')}`; recoverLink.textContent = '前往管理页恢复入队'; providerLinks.append(recoverLink);
      }
      providerLinks.hidden = !providerLinks.childElementCount;
      if (status === 'SUCCEEDED') {
        const quality = qualityFrom(job);
        providerRenderState(quality === 'UNAVAILABLE' ? '任务已完成，但质量批次不可用；请查看质量明细。' : `供应商导入成功，质量状态：${quality}。`, quality === 'UNAVAILABLE' ? 'unavailable' : 'success', requestId);
        providerRefresh.hidden = true;
      } else if (status === 'FAILED') {
        providerRenderState(`供应商导入失败：${job.error_summary || job.error || '服务端未提供详细原因'}。${job.retryable ? '该依赖类失败可从管理页重试。' : '该失败不可盲目重试，请处理数据、字段或权限问题。'}`, 'error', requestId);
        providerRefresh.hidden = true;
      } else {
        providerRenderState(`任务 ${providerJobId || '--'} 当前为 ${statusLabel(status, 'job')}；每 2 秒自动刷新。`, statusKind(status), requestId);
      }
    }

    function stopProviderPolling(completed = false) {
      if (providerPollTimer) window.clearInterval(providerPollTimer);
      providerPollTimer = null;
      providerPollBusy = false;
      if (completed) { providerJobActive = false; updateProviderControls(); }
    }

    function renderProviderPollingTimeout() {
      stopProviderPolling(); providerRefresh.hidden = false;
      providerLinks.replaceChildren();
      const recoverLink = document.createElement('a'); recoverLink.className = 'button-link'; recoverLink.href = `/admin?job_id=${encodeURIComponent(providerJobId || '')}`; recoverLink.textContent = '前往管理页查看任务'; providerLinks.append(recoverLink); providerLinks.hidden = false;
      providerRenderState(`任务 ${providerJobId} 仍在队列中，页面轮询已超时；请确认 worker 正在运行后手动刷新。`, 'unavailable');
    }

    async function refreshProviderJob() {
      if (!providerJobId || providerPollBusy) return null;
      providerPollBusy = true;
      try {
        const payload = await request(`/api/v1/jobs/${encodeURIComponent(providerJobId)}`);
        const job = payload.data || {};
        renderProviderJob(job, payload.request_id);
        const status = String(job.status || '').toUpperCase();
        if (['SUCCEEDED', 'FAILED'].includes(status)) stopProviderPolling(true);
        return status;
      } catch (error) {
        providerRenderState(`任务查询失败：${window.ResearchApp.errorMessage(error.status, error.payload)}；可稍后手动刷新。`, error.status === 403 ? 'permission' : (error.status === 503 ? 'unavailable' : 'error'), error.payload?.request_id);
        providerRefresh.hidden = false;
        stopProviderPolling();
        return null;
      } finally { providerPollBusy = false; }
    }

    function scheduleProviderPolling() {
      if (providerPollTimer || !providerJobId) return;
      const tick = async () => {
        if (Date.now() >= providerPollDeadline) {
          renderProviderPollingTimeout();
          return;
        }
        await refreshProviderJob();
      };
      providerPollTimer = window.setInterval(tick, 2000);
      tick();
    }

    async function resumeProviderPolling() {
      if (!providerJobId || providerPollBusy) return;
      providerJobActive = true;
      providerPollDeadline = Date.now() + 60000;
      providerRefresh.hidden = true;
      updateProviderControls();
      providerRenderState(`正在恢复任务 ${providerJobId} 的轮询；请稍候。`, 'loading');
      const status = await refreshProviderJob();
      if (status && !['SUCCEEDED', 'FAILED'].includes(status) && providerJobActive) scheduleProviderPolling();
    }

    function startProviderPolling(jobId, requestId) {
      providerJobId = jobId;
      providerJobActive = true;
      providerPollDeadline = Date.now() + 60000;
      providerRefresh.hidden = true;
      renderProviderJob({ job_id: jobId, status: 'QUEUED', attempt: 1, run_number: 1, phase: 'provider-import' }, requestId);
      scheduleProviderPolling();
    }

    async function loadProviderCapabilities() {
      try {
        const payload = await request('/api/v1/admin/data-imports/capabilities');
        renderProviderCapabilities(payload, payload.request_id);
      } catch (error) {
        providerCapabilities.textContent = `供应商能力读取失败：${window.ResearchApp.errorMessage(error.status, error.payload)}`;
        providerCapabilities.className = `state ${error.status === 403 ? 'permission' : 'unavailable'}`;
        providerSubmit.disabled = true;
        providerRenderState('无法确认供应商或队列状态；请刷新页面，服务端不会在未知配置下猜测或切换主源。', error.status === 403 ? 'permission' : 'unavailable', error.payload?.request_id);
      }
    }

    providerSelect.addEventListener('change', updateProviderControls);
    providerScope.addEventListener('change', updateProviderControls);
    providerRefresh.addEventListener('click', resumeProviderPolling);
    async function submitProviderImport() {
      const values = providerPendingValues;
      if (!values || providerSubmitting) return;
      providerSubmitting = true; providerSubmit.disabled = true; $('#provider-import-confirm').disabled = true; providerSubmit.textContent = '正在提交…'; providerRenderState('正在建立持久化 queued 任务并提交队列；请勿重复提交。', 'loading');
      try {
        const payload = await request(`/api/v1/admin/data-imports/${encodeURIComponent(values.provider)}/${encodeURIComponent(values.business_date)}?scope=${encodeURIComponent(values.scope)}`, { method: 'POST' });
        const job = payload.data || {}; startProviderPolling(job.job_id || job.id, payload.request_id); setProviderConfirmationMode(false); providerPendingValues = null;
      } catch (error) {
        providerRenderState(`提交失败：${window.ResearchApp.errorMessage(error.status, error.payload)}。任务若已落库，请前往管理页确认并恢复。`, error.status === 403 ? 'permission' : (error.status === 503 ? 'unavailable' : 'error'), error.payload?.request_id); $('#provider-import-confirm').disabled = false;
      }
      providerSubmitting = false; updateProviderControls(); providerSubmit.textContent = providerConfirming ? '返回修改' : '提交供应商导入';
    }
    providerForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (providerSubmitting || !admin) return;
      if (providerConfirming) { setProviderConfirmationMode(false); providerPendingValues = null; providerRenderState('已返回修改，尚未提交供应商导入。', 'unavailable'); providerSubmit.focus(); return; }
      const capability = capabilityFor();
      const businessDate = String(providerDate.value || '').trim();
      if (!businessDate) { providerRenderState('请选择交易日期。', 'error'); providerDate.focus(); return; }
      if (!capability.enabled || !queueIsAvailable()) { providerRenderState('供应商或队列当前不可用，任务尚未提交。', 'unavailable'); return; }
      if (providerScope.value === 'full' && !(capability.full_open ?? capability.full_enabled)) { providerRenderState(capability.full_reason || 'full 未开放。', 'unavailable'); return; }
      providerPendingValues = { provider: providerSelect.value, business_date: businessDate, scope: providerScope.value };
      const summary = $('#provider-import-confirmation-summary'); appendSummary(summary, [['对象', '供应商行情导入'], ['供应商 / 范围', `${providerPendingValues.provider} / ${providerPendingValues.scope}`], ['交易日期', providerPendingValues.business_date], ['队列', '服务端持久化任务；随后由 worker 执行']]);
      setProviderConfirmationMode(true); providerRenderState('请核对摘要；点击“确认提交”后才会创建供应商任务。', 'warning');
    });
    $('#provider-import-confirm').addEventListener('click', submitProviderImport);

    const today = new Date(); providerDate.value = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
    loadProviderCapabilities();
  }
}());
