(function () {
  const root = document.querySelector('[data-page="data-import"]');
  if (!root) return;
  const $ = (selector) => root.querySelector(selector);
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const roles = () => {
    try {
      const token = window.ResearchApp.getToken();
      const value = token && JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/').padEnd(token.split('.')[1].length + (4 - token.split('.')[1].length % 4) % 4, '=')));
      return value && Array.isArray(value.roles) ? value.roles : [];
    } catch (_) { return []; }
  };
  const request = async (url, options) => window.ResearchApp.readJson(await window.ResearchApp.apiFetch(url, options));
  const state = (text, kind, requestId) => window.ResearchApp.renderState($('#data-import-state'), kind || '', text, requestId);
  const iso = (value) => value ? new Date(value).toISOString() : value;
  const renderIssues = (summary) => {
    const issues = summary && (summary.errors || summary.issues || summary.failures || summary.warnings);
    $('#data-import-issues').innerHTML = (Array.isArray(issues) && issues.length ? issues : ['未发现质量错误或警告。']).map((issue) => `<li>${esc(typeof issue === 'string' ? issue : `${issue.code || '质量规则'}：${issue.message || issue.detail || JSON.stringify(issue)}`)}</li>`).join('');
  };
  const render = (record, requestId) => {
    const quality = record.quality_summary || {};
    $('#data-import-result').innerHTML = [['批次 ID', record.batch_id], ['质量状态', record.quality_status], ['记录数', record.record_count], ['版本', record.version]].map(([key, value]) => `<dt>${key}</dt><dd><code>${esc(value ?? '未提供')}</code></dd>`).join('');
    renderIssues(quality);
    const links = $('#data-import-links');
    links.innerHTML = `<a class="button-link" href="/data-quality?batch_id=${encodeURIComponent(record.batch_id)}">查看质量明细</a>`;
    links.hidden = false;
    state(`导入完成，质量状态：${record.quality_status || '未提供'}。`, record.quality_status === 'UNAVAILABLE' ? 'error' : 'success', requestId);
  };
  const admin = roles().includes('ADMIN');
  $('#data-import-permission').textContent = admin ? '当前账号具备管理员权限。' : '需要管理员权限才能导入授权行情；服务端会拒绝无权请求。';
  $('#data-import-submit').disabled = !admin;
  $('#data-import-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget).entries());
    values.available_at = iso(values.available_at);
    values.information_cutoff_at = iso(values.information_cutoff_at);
    const version = String(values.version || '').trim();
    if (version) values.version = version;
    else delete values.version;
    if (!values.file_location.trim() || !values.source_name.trim() || !values.license_note.trim()) return state('请填写本地文件路径、来源和授权说明。', 'error');
    $('#data-import-submit').disabled = true;
    state('正在导入并执行质量校验…', 'loading');
    try {
      const payload = await request('/api/v1/data/imports', { method: 'POST', body: values });
      render(payload.data || {}, payload.request_id);
    } catch (error) {
      const details = error.payload && error.payload.error && error.payload.error.details;
      const failure = Array.isArray(details) ? details[0] : null;
      if (failure && failure.batch_id) render({ batch_id: failure.batch_id, quality_status: 'UNAVAILABLE', quality_summary: failure.quality || {} });
      state(error.message || '导入失败；请检查文件、授权信息和数据质量。', 'error');
      $('#data-import-submit').disabled = !admin;
    }
  });
}());
