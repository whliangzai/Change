(function () {
  'use strict';
  const root = document.querySelector('[data-page="admin"]');
  if (!root) return;
  const $ = (selector) => root.querySelector(selector);
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const request = async (url, options) => window.ResearchApp.readJson(await window.ResearchApp.apiFetch(url, options));
  const state = (text, type, requestId) => window.ResearchApp.renderState($('#admin-state'), type || '', text, requestId);
  const message = (error) => window.ResearchApp.errorMessage(error.status, error.payload);
  let jobsPage = 1; let auditPage = 1; let selectedJob = null; let retryKey = null; let isRetrying = false;

  function renderFailure(prefix, error, retry) {
    const type = error.status === 403 ? 'permission' : (error.status === 503 ? 'unavailable' : 'error');
    state(`${prefix}${message(error)}`, type, error.payload?.request_id);
    if (error.status === 403) window.ResearchApp.renderState($('#admin-permission'), 'permission', '当前角色缺少管理或审计权限；受限任务详情不会显示。', error.payload?.request_id);
    if (retry) { const button = document.createElement('button'); button.type = 'button'; button.textContent = '重试'; button.addEventListener('click', retry); $('#admin-state').append(document.createTextNode(' '), button); }
  }

  function pager(target, page, data, load) {
    const element = $(target); element.replaceChildren(); const totalPages = Math.max(1, Math.ceil((data.total || 0) / (data.page_size || 50)));
    const label = document.createElement('span'); label.textContent = `第 ${page}/${totalPages} 页，共 ${data.total || 0} 条`; element.append(label);
    [['上一页', page - 1], ['下一页', page + 1]].forEach(([text, next]) => { const button = document.createElement('button'); button.type = 'button'; button.textContent = text; button.disabled = next < 1 || next > totalPages; button.addEventListener('click', () => load(next)); element.append(button); });
  }

  function resetRetry() { selectedJob = null; retryKey = null; isRetrying = false; $('#retry-confirmation').hidden = true; $('#retry-form-error').hidden = true; $('#retry-note').value = ''; }

  function selectRetry(job) {
    selectedJob = job; retryKey = window.ResearchApp.idempotency();
    $('#retry-target').textContent = `目标对象：${job.job_id || job.id}；类型：${job.job_type || job.type || '--'}；当前状态：${job.status || '--'}；失败原因：${job.error || job.failure_reason || '--'}。`;
    $('#retry-confirmation').hidden = false; $('#retry-confirm').disabled = false; $('#retry-confirm').textContent = $('#retry-confirm').dataset.submitLabel;
    $('#retry-confirm').focus();
  }

  async function retrySelected() {
    if (!selectedJob || isRetrying) return;
    isRetrying = true; const button = $('#retry-confirm'); button.disabled = true; button.textContent = '正在请求重试…';
    state('正在向服务端请求重试任务；服务端将保留审计和幂等处理。', 'loading');
    try {
      const payload = await request(`/api/v1/jobs/${encodeURIComponent(selectedJob.job_id || selectedJob.id)}/retry`, { method: 'POST', headers: { 'Idempotency-Key': retryKey } });
      state(`任务 ${payload.data?.job_id || selectedJob.job_id || selectedJob.id} 已由服务端受理，当前状态 ${payload.data?.status || '待刷新'}。`, 'success', payload.request_id);
      resetRetry(); await loadJobs(jobsPage); await loadAudit(auditPage);
    } catch (error) {
      const formError = $('#retry-form-error'); formError.textContent = message(error); formError.hidden = false;
      renderFailure('重试请求未完成：', error, retrySelected); isRetrying = false; button.disabled = false; button.textContent = button.dataset.submitLabel;
    }
  }

  async function loadJobs(page) {
    jobsPage = page || 1;
    try {
      const payload = await request(`/api/v1/jobs?page=${jobsPage}&page_size=50`); const data = payload.data || {}; const items = data.items || []; const body = $('#jobs-table tbody'); body.replaceChildren();
      items.forEach((job) => {
        const id = job.job_id || job.id; const row = document.createElement('tr');
        row.innerHTML = `<td><code>${esc(id || '--')}</code></td><td>${esc(job.object_type || job.job_type || job.type || '--')}</td><td>${esc(job.status || '--')}</td><td>${esc(job.error || job.failure_reason || '--')}</td><td class="date-value">${esc(job.created_at || '--')}</td><td></td>`;
        const button = document.createElement('button'); button.type = 'button'; button.textContent = '查看重试影响'; button.disabled = !id || !['FAILED', 'UNAVAILABLE'].includes(job.status); button.addEventListener('click', () => selectRetry(job)); row.lastElementChild.append(button); body.append(row);
      });
      if (!items.length) body.innerHTML = '<tr><td colspan="6" class="muted">没有可见任务。当前角色无权限时，服务端不会返回受限任务详情。</td></tr>';
      $('#jobs-result-count').textContent = `任务结果：${data.total || 0} 条，当前第 ${data.page || jobsPage} 页。`; pager('#jobs-pagination', jobsPage, data, loadJobs);
      window.ResearchApp.renderState($('#admin-permission'), 'success', '服务端已返回当前会话可见的任务范围。', payload.request_id); state('任务状态已加载。', 'success', payload.request_id);
    } catch (error) { $('#jobs-result-count').textContent = '任务结果暂不可用。'; renderFailure('任务查询：', error, () => loadJobs(jobsPage)); }
  }

  async function loadAudit(page) {
    auditPage = page || 1;
    try {
      const payload = await request(`/api/v1/audit-events?page=${auditPage}&page_size=50`); const data = payload.data || {}; const items = data.items || []; const body = $('#audit-table tbody'); body.replaceChildren();
      items.forEach((item) => { const row = document.createElement('tr'); row.innerHTML = `<td class="date-value">${esc(item.occurred_at || '--')}</td><td><code>${esc(item.actor_id || '--')}</code></td><td>${esc((item.actor_roles || []).join(', ') || '--')}</td><td>${esc(item.action || '--')}</td><td><code>${esc(`${item.object_type || '--'} ${item.object_id || '--'}`)}</code></td><td>${esc(item.result || '--')}</td><td><code>${esc(item.request_id || '--')}</code></td>`; body.append(row); });
      if (!items.length) body.innerHTML = '<tr><td colspan="7" class="muted">没有可见审计事件。可在完成管理操作后刷新查看服务端审计结果。</td></tr>';
      $('#audit-result-count').textContent = `审计结果：${data.total || 0} 条，当前第 ${data.page || auditPage} 页。`; pager('#audit-pagination', auditPage, data, loadAudit);
    } catch (error) { $('#audit-result-count').textContent = '审计结果暂不可用。'; renderFailure('审计查询：', error, () => loadAudit(auditPage)); }
  }

  async function loadAll() { state('正在加载管理数据…', 'loading'); await Promise.all([loadJobs(jobsPage), loadAudit(auditPage)]); }
  $('#admin-refresh').addEventListener('click', loadAll); $('#retry-cancel').addEventListener('click', resetRetry); $('#retry-confirm').addEventListener('click', retrySelected); loadAll();
}());
