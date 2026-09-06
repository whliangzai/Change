(function () {
  "use strict";

  const root = document.querySelector('[data-page="data-quality"]');
  if (!root) return;

  const $ = (selector) => root.querySelector(selector);
  const filters = $("#batch-filters");
  const state = $("#batch-state");
  const table = $("#batch-table");
  const tbody = $("#batch-table tbody");
  const pager = $("#batch-pagination");
  const detail = $("#quality-detail");
  const resultCount = $("#batch-result-count");
  let activeBatchRequest = 0;
  let activeDetailRequest = 0;
  let detailTrigger = null;

  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[character]));

  function display(value) {
    return value === null || value === undefined || value === "" ? "--" : String(value);
  }

  function formatNumber(value) {
    if (value === null || value === undefined || value === "") return "--";
    const number = Number(value);
    return Number.isFinite(number) ? new Intl.NumberFormat("zh-CN").format(number) : display(value);
  }

  function requestId(error) {
    return error?.payload?.request_id || error?.payload?.error?.request_id || "";
  }

  async function request(url) {
    return window.ResearchApp.readJson(await window.ResearchApp.apiFetch(url));
  }

  function renderState(target, kind, message, id = "") {
    window.ResearchApp.renderState(target, kind, message, id);
  }

  function renderTableMessage(colspan, message) {
    tbody.replaceChildren();
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = colspan;
    cell.textContent = message;
    row.append(cell);
    tbody.append(row);
  }

  function renderFailure(target, error, retry, objectName) {
    const status = error?.status;
    const message = status === 403
      ? `当前账号无权查看${objectName}，请联系管理员确认研究权限。`
      : status === 503
        ? `${objectName}服务暂不可用，请确认授权数据服务后重试。`
        : status === 401
          ? "登录已失效，正在返回登录页。"
          : error?.message || "请求失败，请稍后重试。";
    const kind = status === 403 ? "permission" : status === 503 ? "unavailable" : "error";
    renderState(target, kind, message, requestId(error));
    if (status !== 401) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "重试";
      button.addEventListener("click", retry);
      target.append(document.createTextNode(" "), button);
    }
  }

  function filterLabel() {
    const statusLabel = $("#batch-status").selectedOptions[0]?.textContent || "全部";
    return `质量状态：${statusLabel}；每页：${$("#batch-page-size").value} 条`;
  }

  function renderPager(page, pageSize, total) {
    pager.replaceChildren();
    if (!total) return;
    const pages = Math.max(1, Math.ceil(total / pageSize));
    const summary = document.createElement("span");
    summary.textContent = `第 ${page}/${pages} 页，共 ${total} 条`;
    pager.append(summary);
    [["上一页", page - 1], ["下一页", page + 1]].forEach(([label, next]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.disabled = next < 1 || next > pages;
      button.addEventListener("click", () => loadBatches(next));
      pager.append(button);
    });
  }

  function renderBatches(payload) {
    const data = payload?.data || {};
    const items = Array.isArray(data.items) ? data.items : [];
    const total = Number(data.total || 0);
    const page = Number(data.page || 1);
    const pageSize = Number(data.page_size || $("#batch-page-size").value);
    tbody.replaceChildren();

    if (!items.length) {
      renderTableMessage(8, "当前筛选没有数据批次。");
      const reason = $("#batch-status").value
        ? "未找到该质量状态的批次。调整筛选条件或等待授权行情导入完成。"
        : "当前账号尚无可见的数据批次。调整筛选条件或等待授权行情导入完成。";
      renderState(state, "empty", reason);
      resultCount.textContent = `当前筛选：${filterLabel()}；共 0 条`;
    } else {
      items.forEach((item) => {
        const row = document.createElement("tr");
        row.innerHTML = `<td><code>${esc(display(item.batch_id))}</code></td><td class="date-value">${esc(display(item.data_date))}</td><td>${esc(display(item.source_name))}</td><td>${esc(display(item.data_type))}</td><td>${esc(display(item.version))}</td><td class="numeric">${esc(formatNumber(item.record_count))}</td><td><span class="status status-${esc(String(item.quality_status || "unknown").toLowerCase())}">${esc(display(item.quality_status))}</span></td><td><button type="button" data-batch-id="${esc(item.batch_id)}">查看质量</button></td>`;
        const button = row.querySelector("button");
        button.addEventListener("click", () => loadQuality(item.batch_id, button));
        tbody.append(row);
      });
      renderState(state, "success", `已加载本页 ${items.length} 个数据批次。`);
      resultCount.textContent = `当前筛选：${filterLabel()}；共 ${total} 条`;
    }
    table.setAttribute("aria-busy", "false");
    renderPager(page, pageSize, total);
  }

  async function loadBatches(page = 1) {
    const requestNumber = ++activeBatchRequest;
    table.setAttribute("aria-busy", "true");
    renderState(state, "loading", "正在加载数据批次，列表会保留在当前位置…");
    const query = new URLSearchParams({ page: String(page), page_size: $("#batch-page-size").value });
    if ($("#batch-status").value) query.set("status", $("#batch-status").value);
    try {
      const payload = await request(`/api/v1/data/batches?${query}`);
      if (requestNumber !== activeBatchRequest) return;
      renderBatches(payload);
    } catch (error) {
      if (requestNumber !== activeBatchRequest) return;
      table.setAttribute("aria-busy", "false");
      pager.replaceChildren();
      renderTableMessage(8, "无法加载数据批次。请查看页面提示后重试。");
      resultCount.textContent = `当前筛选：${filterLabel()}；结果未加载`;
      renderFailure(state, error, () => loadBatches(page), "数据批次");
    }
  }

  function appendSummary(label, value) {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = display(value);
    $("#quality-summary").append(dt, dd);
  }

  function renderIssues(summary) {
    const issuesList = $("#quality-issues");
    issuesList.replaceChildren();
    const issues = summary.errors || summary.issues || summary.failures || summary.warnings || [];
    const normalized = Array.isArray(issues)
      ? issues
      : Object.entries(issues).map(([code, message]) => ({ code, message }));
    normalized.forEach((issue) => {
      const item = document.createElement("li");
      item.textContent = typeof issue === "string"
        ? issue
        : `${issue.code || "质量规则"}：${issue.message || issue.detail || JSON.stringify(issue)}`;
      issuesList.append(item);
    });
    if (!normalized.length) {
      const item = document.createElement("li");
      item.textContent = "未发现质量错误或警告。";
      issuesList.append(item);
    }
  }

  async function loadQuality(batchId, trigger = null) {
    const requestNumber = ++activeDetailRequest;
    detailTrigger = trigger || detailTrigger;
    detail.hidden = false;
    detail.focus();
    $("#quality-summary").replaceChildren();
    $("#quality-issues").replaceChildren();
    $("#quality-request-id").textContent = "";
    renderState($("#quality-state"), "loading", "正在加载质量明细…");
    try {
      const payload = await request(`/api/v1/data/batches/${encodeURIComponent(batchId)}/quality`);
      if (requestNumber !== activeDetailRequest) return;
      const data = payload?.data || {};
      [["批次", data.batch_id], ["状态", data.quality_status], ["数据日期", data.data_date], ["记录数", formatNumber(data.record_count)], ["来源", data.source_name], ["版本", data.version]].forEach(([label, value]) => appendSummary(label, value));
      renderIssues(data.quality_summary || {});
      renderState($("#quality-state"), "success", "质量明细已加载。", payload?.request_id || "");
      $("#quality-request-id").textContent = payload?.request_id ? `请求号：${payload.request_id}` : "";
    } catch (error) {
      if (requestNumber !== activeDetailRequest) return;
      renderFailure($("#quality-state"), error, () => loadQuality(batchId, detailTrigger), "该批次质量明细");
    }
  }

  filters.addEventListener("submit", (event) => {
    event.preventDefault();
    loadBatches(1);
  });
  $("#batch-reset").addEventListener("click", () => {
    filters.reset();
    loadBatches(1);
  });
  $("#quality-close").addEventListener("click", () => {
    detail.hidden = true;
    detailTrigger?.focus();
  });

  const initialQuality = (root.dataset.apiUrl || "").match(/\/data\/batches\/([^/]+)\/quality$/);
  loadBatches().then(() => {
    if (initialQuality) loadQuality(decodeURIComponent(initialQuality[1]));
  });
}());
