(function () {
  const root = document.querySelector('[data-page="data-quality"]');
  if (!root) return;
  const $ = (selector) => root.querySelector(selector);
  const state = $('#batch-state');
  const tbody = $('#batch-table tbody');
  const pager = $('#batch-pagination');
  const detail = $('#quality-detail');
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const messageFor = (status) => ({ 401: '登录已失效，请重新登录。', 403: '当前账号无权查看此数据。', 409: '请求与当前数据状态冲突，请刷新后重试。', 422: '查询条件不合法，请检查筛选项。', 503: '数据服务暂不可用，请确认后端与数据库状态。' }[status] || '接口请求失败，请稍后重试。');
  async function request(url) {
    return window.ResearchApp.readJson(await window.ResearchApp.apiFetch(url));
  }
  function renderPager(target, page, pageSize, total, load) {
    target.replaceChildren(); if (!total) return;
    const pages = Math.max(1, Math.ceil(total / pageSize));
    const text = document.createElement('span'); text.textContent = `第 ${page}/${pages} 页，共 ${total} 条`;
    target.append(text);
    [['上一页', page - 1], ['下一页', page + 1]].forEach(([label, next]) => { const button = document.createElement('button'); button.type = 'button'; button.textContent = label; button.disabled = next < 1 || next > pages; button.addEventListener('click', () => load(next)); target.append(button); });
  }
  function renderBatches(payload) {
    const data = payload?.data || {}; const items = data.items || []; tbody.replaceChildren();
    items.forEach((item) => { const row = document.createElement('tr'); row.innerHTML = `<td><code>${esc(item.batch_id)}</code></td><td>${esc(item.data_date)}</td><td>${esc(item.source_name)}</td><td>${esc(item.data_type)}</td><td>${esc(item.version)}</td><td>${esc(item.record_count ?? 0)}</td><td><span class="status status-${esc(String(item.quality_status || '').toLowerCase())}">${esc(item.quality_status)}</span></td><td><button type="button" data-batch-id="${esc(item.batch_id)}">查看质量</button></td>`; row.querySelector('button').addEventListener('click', () => loadQuality(item.batch_id)); tbody.append(row); });
    state.textContent = items.length ? `已加载 ${items.length} 个批次` : '暂无符合条件的数据批次。';
    state.dataset.kind = items.length ? 'success' : 'empty';
    renderPager(pager, data.page || 1, data.page_size || 50, data.total || 0, loadBatches);
  }
  async function loadBatches(page = 1) { state.textContent = '正在加载数据批次…'; state.dataset.kind = 'loading'; const query = new URLSearchParams({ page, page_size: $('#batch-page-size').value }); if ($('#batch-status').value) query.set('status', $('#batch-status').value); try { renderBatches(await request(`/api/v1/data/batches?${query}`)); } catch (error) { tbody.replaceChildren(); pager.replaceChildren(); state.textContent = error.message || messageFor(error.status); state.dataset.kind = 'error'; if (error.status === 401) setTimeout(() => { window.location.href = '/login'; }, 800); } }
  async function loadQuality(batchId) { detail.hidden = false; $('#quality-state').textContent = '正在加载质量明细…'; $('#quality-summary').replaceChildren(); $('#quality-issues').replaceChildren(); try { const payload = await request(`/api/v1/data/batches/${encodeURIComponent(batchId)}/quality`); const data = payload?.data || {}; const summary = data.quality_summary || {}; const dl = $('#quality-summary'); [['批次', data.batch_id], ['状态', data.quality_status], ['数据日期', data.data_date], ['记录数', data.record_count], ['来源', data.source_name]].forEach(([label, value]) => { const dt = document.createElement('dt'); dt.textContent = label; const dd = document.createElement('dd'); dd.textContent = value ?? '未提供'; dl.append(dt, dd); }); const issues = summary.errors || summary.issues || summary.failures || []; (Array.isArray(issues) ? issues : Object.entries(issues).map(([code, value]) => ({ code, message: value }))).forEach((issue) => { const li = document.createElement('li'); li.textContent = typeof issue === 'string' ? issue : `${issue.code || '质量规则'}：${issue.message || issue.detail || JSON.stringify(issue)}`; $('#quality-issues').append(li); }); if (!issues.length) { const li = document.createElement('li'); li.textContent = '未发现质量错误或警告。'; $('#quality-issues').append(li); } $('#quality-state').textContent = '质量明细已加载'; $('#quality-request-id').textContent = payload?.request_id ? `request_id: ${payload.request_id}` : ''; } catch (error) { $('#quality-state').textContent = error.message || messageFor(error.status); if (error.status === 401) window.location.href = '/login'; } }
  $('#batch-filters').addEventListener('submit', (event) => { event.preventDefault(); loadBatches(1); }); $('#quality-close').addEventListener('click', () => { detail.hidden = true; });
  const initialQuality = (root.dataset.apiUrl || '').match(/\/data\/batches\/([^/]+)\/quality$/);
  loadBatches().then(() => { if (initialQuality) loadQuality(initialQuality[1]); });
}());
