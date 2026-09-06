(function () {
  "use strict";

  const root = document.querySelector('[data-page="universe"]');
  if (!root) return;

  const $ = (selector) => root.querySelector(selector);
  const poolFilters = $("#universe-filters");
  const poolTable = $("#pool-table");
  const barsTable = $("#bars-table");
  let activePoolRequest = 0;
  let activeBarsRequest = 0;

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

  function formatNumber(value, options = {}) {
    if (value === null || value === undefined || value === "") return "--";
    const number = Number(value);
    return Number.isFinite(number) ? new Intl.NumberFormat("zh-CN", options).format(number) : display(value);
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

  function renderTableMessage(target, colspan, message) {
    const body = target.querySelector("tbody");
    body.replaceChildren();
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = colspan;
    cell.textContent = message;
    row.append(cell);
    body.append(row);
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

  function renderPager(target, data, load) {
    target.replaceChildren();
    const total = Number(data.total || 0);
    if (!total) return;
    const page = Number(data.page || 1);
    const pageSize = Number(data.page_size || 50);
    const pages = Math.max(1, Math.ceil(total / pageSize));
    const label = document.createElement("span");
    label.textContent = `第 ${page}/${pages} 页，共 ${total} 条`;
    target.append(label);
    [["上一页", page - 1], ["下一页", page + 1]].forEach(([text, next]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = text;
      button.disabled = next < 1 || next > pages;
      button.addEventListener("click", () => load(next));
      target.append(button);
    });
  }

  function poolFilterLabel() {
    const status = $("#pool-status").selectedOptions[0]?.textContent || "全部";
    return `历史交易日：${$("#trade-date").value || "--"}；状态：${status}；每页：${$("#pool-page-size").value} 条`;
  }

  function barsFilterLabel() {
    const symbol = $("#bar-symbol").value.trim();
    return `历史交易日：${$("#trade-date").value || "--"}${symbol ? `；代码：${symbol}` : "；全部代码"}`;
  }

  function renderPool(payload) {
    const data = payload?.data || {};
    const items = Array.isArray(data.items) ? data.items : [];
    const body = poolTable.querySelector("tbody");
    const total = Number(data.total || 0);
    body.replaceChildren();
    if (!items.length) {
      renderTableMessage(poolTable, 9, "当前筛选没有股票池记录。");
      const reason = $("#pool-status").value
        ? "该历史交易日没有符合入选状态的记录。调整筛选条件或确认授权数据是否已导入。"
        : "该历史交易日暂无股票池记录。调整交易日或确认授权数据是否已导入。";
      renderState($("#pool-state"), "empty", reason);
      $("#pool-result-count").textContent = `当前筛选：${poolFilterLabel()}；共 0 条`;
    } else {
      items.forEach((item) => {
        const row = document.createElement("tr");
        const reasons = Array.isArray(item.exclusion_reasons)
          ? item.exclusion_reasons.join("；")
          : item.exclusion_reasons;
        row.innerHTML = `<td><code>${esc(display(item.symbol))}</code></td><td>${esc(display(item.exchange))}</td><td>${esc(display(item.board))}</td><td><span class="status">${item.in_pool ? "入选" : "排除"}</span></td><td>${esc(display(item.status_as_of))}</td><td><code>${esc(display(item.source_batch_id))}</code></td><td class="numeric">${esc(formatNumber(item.listed_trade_days))}</td><td class="numeric">${esc(formatNumber(item.amount_median_20, { minimumFractionDigits: 2, maximumFractionDigits: 2 }))}</td><td>${esc(display(reasons || "无"))}</td>`;
        body.append(row);
      });
      renderState($("#pool-state"), "success", `已加载本页 ${items.length} 条股票池记录。`);
      $("#pool-result-count").textContent = `当前筛选：${poolFilterLabel()}；共 ${total} 条`;
    }
    poolTable.setAttribute("aria-busy", "false");
    renderPager($("#pool-pagination"), data, loadPool);
  }

  async function loadPool(page = 1) {
    const requestNumber = ++activePoolRequest;
    poolTable.setAttribute("aria-busy", "true");
    renderState($("#pool-state"), "loading", "正在加载该交易日的股票池，列表会保留在当前位置…");
    const query = new URLSearchParams({
      trade_date: $("#trade-date").value,
      page: String(page),
      page_size: $("#pool-page-size").value,
    });
    if ($("#pool-status").value) query.set("status", $("#pool-status").value);
    try {
      const payload = await request(`/api/v1/securities/pool?${query}`);
      if (requestNumber !== activePoolRequest) return;
      renderPool(payload);
    } catch (error) {
      if (requestNumber !== activePoolRequest) return;
      poolTable.setAttribute("aria-busy", "false");
      $("#pool-pagination").replaceChildren();
      renderTableMessage(poolTable, 9, "无法加载股票池。请查看页面提示后重试。");
      $("#pool-result-count").textContent = `当前筛选：${poolFilterLabel()}；结果未加载`;
      renderFailure($("#pool-state"), error, () => loadPool(page), "股票池");
    }
  }

  function renderBars(payload) {
    const data = payload?.data || {};
    const items = Array.isArray(data.items) ? data.items : [];
    const body = barsTable.querySelector("tbody");
    const total = Number(data.total || 0);
    body.replaceChildren();
    if (!items.length) {
      renderTableMessage(barsTable, 9, "当前筛选没有授权日线行情。");
      renderState($("#bars-state"), "empty", "该历史交易日暂无授权日线行情。调整交易日或股票代码后重试。");
      $("#bars-result-count").textContent = `当前筛选：${barsFilterLabel()}；共 0 条`;
    } else {
      const priceOptions = { minimumFractionDigits: 2, maximumFractionDigits: 4 };
      items.forEach((item) => {
        const row = document.createElement("tr");
        row.innerHTML = `<td><code>${esc(display(item.symbol))}</code></td><td class="date-value">${esc(display(item.trade_date))}</td><td class="numeric">${esc(formatNumber(item.raw_open, priceOptions))}</td><td class="numeric">${esc(formatNumber(item.raw_high, priceOptions))}</td><td class="numeric">${esc(formatNumber(item.raw_low, priceOptions))}</td><td class="numeric">${esc(formatNumber(item.raw_close, priceOptions))}</td><td class="numeric">${esc(formatNumber(item.volume))}</td><td class="numeric">${esc(formatNumber(item.amount, { minimumFractionDigits: 2, maximumFractionDigits: 2 }))}</td><td><code>${esc(display(item.data_batch_id))}</code></td>`;
        body.append(row);
      });
      renderState($("#bars-state"), "success", `已加载本页 ${items.length} 条授权日线行情。`);
      $("#bars-result-count").textContent = `当前筛选：${barsFilterLabel()}；共 ${total} 条`;
    }
    barsTable.setAttribute("aria-busy", "false");
    renderPager($("#bars-pagination"), data, loadBars);
  }

  async function loadBars(page = 1) {
    const requestNumber = ++activeBarsRequest;
    barsTable.setAttribute("aria-busy", "true");
    renderState($("#bars-state"), "loading", "正在加载授权日线行情，列表会保留在当前位置…");
    const query = new URLSearchParams({
      trade_date: $("#trade-date").value,
      page: String(page),
      page_size: $("#pool-page-size").value,
    });
    const symbol = $("#bar-symbol").value.trim();
    if (symbol) query.set("symbol", symbol);
    try {
      const payload = await request(`/api/v1/data/bars?${query}`);
      if (requestNumber !== activeBarsRequest) return;
      renderBars(payload);
    } catch (error) {
      if (requestNumber !== activeBarsRequest) return;
      barsTable.setAttribute("aria-busy", "false");
      $("#bars-pagination").replaceChildren();
      renderTableMessage(barsTable, 9, "无法加载授权日线行情。请查看页面提示后重试。");
      $("#bars-result-count").textContent = `当前筛选：${barsFilterLabel()}；结果未加载`;
      renderFailure($("#bars-state"), error, () => loadBars(page), "授权日线行情");
    }
  }

  poolFilters.addEventListener("submit", (event) => {
    event.preventDefault();
    loadPool(1);
    loadBars(1);
  });
  $("#pool-reset").addEventListener("click", () => {
    poolFilters.reset();
    loadPool(1);
    loadBars(1);
  });
  $("#bars-filters").addEventListener("submit", (event) => {
    event.preventDefault();
    loadBars(1);
  });
  $("#bars-reset").addEventListener("click", () => {
    $("#bar-symbol").value = "";
    loadBars(1);
  });

  loadPool();
  loadBars();
}());
