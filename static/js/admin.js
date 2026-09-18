(function () {
  'use strict';
  const root = document.querySelector('[data-page="admin"]');
  if (!root) return;
  const $ = (selector) => root.querySelector(selector);
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const request = async (url, options) => window.ResearchApp.readJson(await window.ResearchApp.apiFetch(url, options));
  const state = (text, type, requestId) => window.ResearchApp.renderState($('#admin-state'), type || '', text, requestId);
  const message = (error) => window.ResearchApp.errorMessage(error.status, error.payload);
  const statusLabel = (value, domain) => window.ResearchApp.statusLabel ? window.ResearchApp.statusLabel(value, domain) : String(value ?? '--');
  let jobsPage = 1; let auditPage = 1;
  let jobsPageSize = Number($('#jobs-page-size').value); let auditPageSize = Number($('#audit-page-size').value);
  let jobsRequest = 0; let auditRequest = 0;
  let selectedJob = null; let retryKey = null; let isRetrying = false;

  function renderFailure(prefix, error, retry) {
    const type = error.status === 403 ? 'permission' : (error.status === 503 ? 'unavailable' : 'error');
    state(`${prefix}${message(error)}`, type, error.payload?.request_id);
    if (error.status === 403) window.ResearchApp.renderState($('#admin-permission'), 'permission', '当前角色缺少管理或审计权限；受限任务详情不会显示。', error.payload?.request_id);
    if (retry) { const button = document.createElement('button'); button.type = 'button'; button.textContent = '重试'; button.addEventListener('click', retry); $('#admin-state').append(document.createTextNode(' '), button); }
  }

  function pager(target, data, load) {
    const element = $(target);
    const page = Number(data.page);
    const pageSize = Number(data.page_size);
    const total = Number(data.total);
    const totalPages = Math.max(1, Math.ceil(total / pageSize));
    element.replaceChildren();
    element.hidden = total === 0;
    if (!total) return;
    const range = document.createElement('span'); range.className = 'pagination-range'; range.textContent = `第 ${page} / ${totalPages} 页，共 ${total} 条`;
    const controls = document.createElement('span'); controls.className = 'pagination-controls';
    const current = document.createElement('span'); current.className = 'pagination-current'; current.textContent = String(page); current.setAttribute('aria-current', 'page');
    const previous = document.createElement('button'); previous.type = 'button'; previous.className = 'button-quiet'; previous.textContent = '上一页'; previous.disabled = page <= 1; previous.addEventListener('click', () => load(page - 1));
    const next = document.createElement('button'); next.type = 'button'; next.className = 'button-quiet'; next.textContent = '下一页'; next.disabled = page >= totalPages; next.addEventListener('click', () => load(page + 1));
    controls.append(previous, current, next); element.append(range, controls);
  }

  function jobUrl(job) { return job.job_id || job.id; }
  function resetRetry() { selectedJob = null; retryKey = null; isRetrying = false; $('#retry-confirmation').hidden = true; $('#retry-form-error').hidden = true; $('#retry-note').value = ''; }

  function selectRetry(job) {
    selectedJob = job; retryKey = window.ResearchApp.idempotency();
    $('#retry-target').textContent = `目标任务：${jobUrl(job) || '--'}；task key：${job.task_key || '--'}；供应商：${job.provider || '--'}；范围：${job.scope || '--'}；状态：${statusLabel(job.status, 'job')}；失败原因：${job.error_summary || job.error || '未提供'}。`;
    $('#retry-confirmation').hidden = false; $('#retry-confirm').disabled = false; $('#retry-confirm').textContent = $('#retry-confirm').dataset.submitLabel;
    $('#retry-confirm').focus();
  }

  async function retrySelected() {
    if (!selectedJob || isRetrying) return;
    isRetrying = true; const button = $('#retry-confirm'); button.disabled = true; button.textContent = '正在请求重试…';
    state('正在向服务端请求重试任务；服务端将保留审计和幂等处理。', 'loading');
    try {
      const payload = await request(`/api/v1/jobs/${encodeURIComponent(jobUrl(selectedJob))}/retry`, { method: 'POST', headers: { 'Idempotency-Key': retryKey } });
      state(`任务 ${payload.data?.job_id || jobUrl(selectedJob)} 已由服务端受理，当前状态 ${statusLabel(payload.data?.status || 'QUEUED', 'job')}。`, 'success', payload.request_id);
      resetRetry(); await loadJobs(jobsPage); await loadAudit(auditPage);
    } catch (error) {
      const formError = $('#retry-form-error'); formError.textContent = message(error); formError.hidden = false;
      renderFailure('重试请求未完成：', error, retrySelected); isRetrying = false; button.disabled = false; button.textContent = button.dataset.submitLabel;
    }
  }

  function filters() {
    const params = new URLSearchParams({ page: String(jobsPage), page_size: String(jobsPageSize) });
    const provider = $('#jobs-provider').value; const statusValue = $('#jobs-status').value; const businessDate = $('#jobs-business-date').value;
    if (provider) params.set('provider', provider); if (statusValue) params.set('status', statusValue); if (businessDate) params.set('business_date', businessDate);
    return params.toString();
  }

  async function loadJobs(page) {
    jobsPage = page || 1;
    const requestNumber = ++jobsRequest;
    try {
      const payload = await request(`/api/v1/jobs?${filters()}`); const data = payload.data || {}; const items = data.items || []; const body = $('#jobs-table tbody');
      if (requestNumber !== jobsRequest) return;
      body.replaceChildren();
      jobsPage = Number(data.page); jobsPageSize = Number(data.page_size); $('#jobs-page-size').value = String(jobsPageSize);
      const totalPages = Math.max(1, Math.ceil(Number(data.total) / jobsPageSize));
      if (jobsPage > totalPages) { await loadJobs(totalPages); return; }
      items.forEach((job) => {
        const id = jobUrl(job); const status = String(job.status || '--').toUpperCase(); const retryable = status === 'FAILED' && Boolean(job.retryable); const requeueable = status === 'QUEUED' && Boolean(job.requeueable); const recoverable = retryable || requeueable; const row = document.createElement('tr');
        const batch = job.batch_id ? `<a href="/data-quality?batch_id=${encodeURIComponent(job.batch_id)}">${esc(job.batch_id)}</a>` : '--';
        row.innerHTML = `<td><code>${esc(id || '--')}</code><span class="muted"> · </span><code>${esc(job.task_key || '--')}</code></td><td>${esc(job.provider || '--')} / ${esc(job.scope || '--')}</td><td class="date-value">${esc(job.business_date || '--')}</td><td>${esc(statusLabel(status, 'job'))}<span class="muted"> · ${esc(job.phase || '--')}</span></td><td>${esc(job.attempt ?? '--')} / ${esc(job.run_number ?? '--')}</td><td>${batch}<span class="muted"> · ${esc(statusLabel(job.quality_status, 'quality'))}</span></td><td>${esc(job.error_summary || job.error || '--')}</td><td class="date-value">${esc(job.completed_at || job.ended_at || '--')}</td><td></td>`;
        const button = document.createElement('button'); button.type = 'button'; button.textContent = retryable ? '查看重试影响' : (requeueable ? '恢复入队' : (status === 'FAILED' ? '不可重试' : '查看')); button.disabled = !recoverable || !id; button.addEventListener('click', () => selectRetry(job)); row.lastElementChild.append(button); body.append(row);
      });
      if (!items.length) body.innerHTML = '<tr><td colspan="9" class="muted">没有符合筛选条件的任务。当前角色无权限时，服务端不会返回受限任务详情。</td></tr>';
      $('#jobs-result-count').textContent = `共 ${Number(data.total)} 条`; pager('#jobs-pagination', data, loadJobs);
      window.ResearchApp.renderState($('#admin-permission'), 'success', '服务端已返回当前会话可见的任务范围。', payload.request_id); state('任务状态已加载。', 'success', payload.request_id);
      const target = new URLSearchParams(window.location.search).get('job_id'); const match = target && items.find((item) => jobUrl(item) === target); if (match) selectRetry(match);
    } catch (error) { $('#jobs-result-count').textContent = '任务结果暂不可用。'; renderFailure('任务查询：', error, () => loadJobs(jobsPage)); }
  }

  async function loadAudit(page) {
    auditPage = page || 1;
    const requestNumber = ++auditRequest;
    try {
      const payload = await request(`/api/v1/audit-events?page=${auditPage}&page_size=${auditPageSize}`); const data = payload.data || {}; const items = data.items || []; const body = $('#audit-table tbody');
      if (requestNumber !== auditRequest) return;
      body.replaceChildren();
      auditPage = Number(data.page); auditPageSize = Number(data.page_size); $('#audit-page-size').value = String(auditPageSize);
      const totalPages = Math.max(1, Math.ceil(Number(data.total) / auditPageSize));
      if (auditPage > totalPages) { await loadAudit(totalPages); return; }
      items.forEach((item) => { const row = document.createElement('tr'); row.innerHTML = `<td class="date-value">${esc(item.occurred_at || '--')}</td><td><code>${esc(item.actor_id || '--')}</code></td><td>${esc((item.actor_roles || []).join(', ') || '--')}</td><td>${esc(item.action || '--')}</td><td><code>task: ${esc(item.task_key || item.idempotency_key || '--')} · job: ${esc(item.job_id || item.object_id || '--')}</code></td><td>${esc(item.result || '--')}</td>`; body.append(row); });
      if (!items.length) body.innerHTML = '<tr><td colspan="6" class="muted">没有可见审计事件。可在完成管理操作后刷新查看服务端审计结果。</td></tr>';
      $('#audit-result-count').textContent = `共 ${Number(data.total)} 条`; pager('#audit-pagination', data, loadAudit);
    } catch (error) { $('#audit-result-count').textContent = '审计结果暂不可用。'; renderFailure('审计查询：', error, () => loadAudit(auditPage)); }
  }

  async function loadAll() { state('正在加载管理数据…', 'loading'); await Promise.all([loadJobs(jobsPage), loadAudit(auditPage)]); }
  $('#jobs-filters').addEventListener('submit', (event) => { event.preventDefault(); loadJobs(1); });
  $('#jobs-filter-reset').addEventListener('click', () => { $('#jobs-provider').value = ''; $('#jobs-status').value = ''; $('#jobs-business-date').value = ''; loadJobs(1); });
  $('#jobs-page-size').addEventListener('change', () => { jobsPageSize = Number($('#jobs-page-size').value); loadJobs(1); });
  $('#audit-page-size').addEventListener('change', () => { auditPageSize = Number($('#audit-page-size').value); loadAudit(1); });
  $('#admin-refresh').addEventListener('click', loadAll); $('#retry-cancel').addEventListener('click', resetRetry); $('#retry-confirm').addEventListener('click', retrySelected); loadAll();
}());
