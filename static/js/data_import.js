(function () {
  const root = document.querySelector('[data-page="data-import"]');
  if (!root) return;
  const $ = (selector) => root.querySelector(selector);
  const form = $('#data-import-form');
  const submit = $('#data-import-submit');
  let isSubmitting = false;
  const fieldIds = {
    file_location: 'file', source_name: 'source', available_at: 'available',
    information_cutoff_at: 'cutoff', license_note: 'license',
  };
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const request = async (url, options) => window.ResearchApp.readJson(await window.ResearchApp.apiFetch(url, options));
  const state = (text, kind, requestId) => window.ResearchApp.renderState($('#data-import-state'), kind || '', text, requestId);
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
  const mapServerErrors = (error) => {
    const details = error.payload?.error?.details;
    if (!Array.isArray(details)) return;
    details.forEach((detail) => {
      const field = detail.field || (Array.isArray(detail.loc) ? detail.loc.at(-1) : '');
      if (field) setFieldError(field, detail.reason || detail.msg || error.message);
    });
  };
  const renderIssues = (summary) => {
    const issues = summary && (summary.errors || summary.issues || summary.failures || summary.warnings);
    $('#data-import-issues').innerHTML = (Array.isArray(issues) && issues.length ? issues : ['未发现质量错误或警告。']).map((issue) => `<li>${esc(typeof issue === 'string' ? issue : `${issue.code || '质量规则'}：${issue.message || issue.detail || JSON.stringify(issue)}`)}</li>`).join('');
  };
  const render = (record, requestId, blocked) => {
    const quality = record.quality_summary || {};
    $('#data-import-result').innerHTML = [['批次 ID', record.batch_id], ['质量状态', record.quality_status], ['记录数', record.record_count], ['版本', record.version]].map(([key, value]) => `<dt>${key}</dt><dd><code>${esc(value ?? '--')}</code></dd>`).join('');
    renderIssues(quality);
    const links = $('#data-import-links');
    links.innerHTML = record.batch_id ? `<a class="button-link" href="/data-quality?batch_id=${encodeURIComponent(record.batch_id)}">查看质量明细</a>` : '';
    links.hidden = !record.batch_id;
    state(blocked ? '导入已被数据质量闸门阻断；请处理下方问题后重新导入。' : `导入完成，质量状态：${record.quality_status || '未提供'}。`, blocked ? 'unavailable' : 'success', requestId);
  };

  $('#data-import-permission').textContent = admin ? '当前会话显示管理员权限；服务端仍会在提交时授权与审计。' : '当前会话未显示管理员权限，无法提交导入；服务端也会拒绝无权请求。';
  $('#data-import-permission').className = `state ${admin ? 'success' : 'permission'}`;
  submit.disabled = !admin;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (isSubmitting || !admin) return;
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
    isSubmitting = true;
    submit.disabled = true;
    submit.textContent = '正在导入并校验…';
    state('正在导入授权行情并执行质量校验；请勿重复提交。', 'loading');
    try {
      const payload = await request('/api/v1/data/imports', { method: 'POST', body: values });
      render(payload.data || {}, payload.request_id, false);
      isSubmitting = false;
      submit.disabled = !admin;
      submit.textContent = submit.dataset.submitLabel;
    } catch (error) {
      const details = error.payload?.error?.details;
      const blocked = Array.isArray(details) && details.find((detail) => detail.batch_id);
      mapServerErrors(error);
      if (blocked) render({ batch_id: blocked.batch_id, quality_status: 'UNAVAILABLE', quality_summary: blocked.quality || {} }, error.payload?.request_id, true);
      else {
        showFormError(error.message || '导入失败；请检查文件、授权信息和服务状态。');
        state(error.message || '导入失败；请检查文件、授权信息和服务状态。', error.status === 403 ? 'permission' : 'error', error.payload?.request_id);
      }
      isSubmitting = false;
      submit.disabled = !admin;
      submit.textContent = submit.dataset.submitLabel;
    }
  });
}());
