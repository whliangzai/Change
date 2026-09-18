// Dependency-free state regressions. Run: node --test tests/frontend/research_pages.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor() {
    this.children = []; this.listeners = {}; this.fields = {};
    this.value = ''; this.disabled = false; this.hidden = false; this.dataset = {};
    this._text = ''; this.options = []; this.selectedOptions = [{ textContent: '全部' }];
  }
  set textContent(value) { this._text = value; this.children = []; }
  get textContent() { return this._text + this.children.map((child) => child.textContent || '').join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; this._text = ''; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  emit(name) { return this.listeners[name]?.({ preventDefault() {}, currentTarget: this }); }
  querySelector(selector) { return this.fields[selector] ||= new Element(); }
  querySelectorAll() { return []; }
  get lastElementChild() { return this.fields.last ||= new Element(); }
  get elements() { return this.fields; }
  setAttribute() {}
  focus() { this.focused = true; }
}

const flush = () => new Promise((resolve) => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function setup(file, handler, roles = ['USER', 'ADMIN'], dataset = {}) {
  const root = new Element(); root.dataset = dataset;
  const nodes = new Map();
  const get = (selector) => {
    if (!nodes.has(selector)) nodes.set(selector, new Element());
    return nodes.get(selector);
  };
  root.querySelector = get;
  get('#strategy-review').fields.decision = new Element();
  get('#strategy-review').fields.decision.options = ['SUBMIT', 'PUBLISH', 'REJECT'].map((value) => ({ value }));
  get('#strategy-review').fields.review_note = new Element();
  get('#strategy-review').review_note = get('#strategy-review').fields.review_note;
  get('#strategy-review').decision = get('#strategy-review').fields.decision;
  get('#batch-page-size').value = '50';
  get('#jobs-page-size').value = '25';
  get('#audit-page-size').value = '25';
  const calls = [];
  const context = {
    document: {
      querySelector: () => root, createElement: () => new Element(),
      createDocumentFragment: () => new Element(), createTextNode: (text) => ({ textContent: text }),
    },
    window: {
      location: { search: '' },
      ResearchApp: {
        apiFetch: (url, options) => { calls.push({ url, options }); return handler(url, options); },
        readJson: async (payload) => payload,
        renderState: (target, kind, text) => { target.hidden = false; target.className = kind; target.textContent = text; },
        errorMessage: () => '请求失败', idempotency: () => 'test-key',
        getToken: () => `test.${Buffer.from(JSON.stringify({ roles })).toString('base64url')}.test`,
      },
    },
    Option: class extends Element { constructor(label, value) { super(); this.textContent = label; this.value = value; } },
    atob: (value) => Buffer.from(value, 'base64').toString(),
    URLSearchParams, Intl, location: {}, navigator: {},
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/js', file), 'utf8'), context);
  return { get, calls };
}

function setupOrderPlans(handler) {
  const root = new Element(); root.dataset = { apiUrl: '/api/v1/order-plans' };
  const nodes = new Map();
  const get = (selector) => {
    if (!nodes.has(selector)) nodes.set(selector, new Element());
    return nodes.get(selector);
  };
  root.querySelector = get;
  const form = get('#plan-decision-form');
  form.plan_id = new Element();
  form.expected_version = new Element();
  form.review_note = new Element();
  form.decision = new Element();
  form.decision.value = 'CONFIRM';
  form.decision.options = [{ value: 'CONFIRM', disabled: false }, { value: 'SKIP', disabled: false }];
  const controls = [form.review_note, form.decision, get('#plan-decision-submit'), get('#plan-decision-confirm'), get('#plan-decision-cancel')];
  form.querySelectorAll = (selector) => selector === 'button, select, textarea' ? controls : [];
  get('#plan-execution-date').value = '2026-09-18';
  const calls = [];
  class FakeFormData {
    constructor() {
      this.values = {
        plan_id: form.plan_id.value,
        expected_version: form.expected_version.value,
        decision: form.decision.value,
        review_note: form.review_note.value,
      };
    }
    entries() { return Object.entries(this.values)[Symbol.iterator](); }
  }
  const context = {
    document: { querySelector: () => root, createElement: () => new Element() },
    window: {
      location: { pathname: '/order-plans', search: '?execution_date=2026-09-18' },
      ResearchApp: {
        apiFetch: (url, options) => { calls.push({ url, options }); return handler(url, options); },
        readJson: async (payload) => payload,
        renderState: (target, kind, text, requestId) => { target.hidden = false; target.className = kind; target.textContent = text; target.requestId = requestId; },
        errorMessage: () => '请求失败', idempotency: () => 'decision-key',
        statusLabel: (value) => String(value ?? '--'), statusClass: () => 'neutral',
      },
    },
    FormData: FakeFormData, URLSearchParams, location: { pathname: '/order-plans', search: '?execution_date=2026-09-18' },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/js/order_plans.js'), 'utf8'), context);
  return { get, calls, controls, form };
}

test('reports ignore stale responses and never use simulation cost as actual deviation', async () => {
  const pending = {};
  const ui = setup('reports.js', (url) => {
    if (url.includes('?')) return Promise.resolve({ data: { items: [{ run_id: 'A' }, { run_id: 'B' }, { run_id: 'C' }], total: 3 } });
    const id = url.split('/')[4];
    if (!url.endsWith('/report')) return Promise.resolve({ data: { run_id: id, result_usable: true } });
    pending[id] = deferred(); return pending[id].promise;
  }, undefined, { apiUrl: '/api/v1/backtests' });
  await flush();
  async function select(id) {
    ui.get('#report-run-select').value = id;
    ui.get('#report-run-select').emit('change');
    ui.get('#report-load').emit('click');
    await flush();
  }
  await select('A');
  await select('B');
  pending.B.resolve({ data: { run_id: 'B', result_usable: true, metrics: { return: '0.1' }, cost: { total: 12 } } });
  await flush();
  assert.equal(ui.get('#report-content').hidden, false);
  assert.match(ui.get('#report-summary').textContent, /B/);
  assert.match(ui.get('#report-cost-table tbody').textContent, /12/);
  assert.match(ui.get('#report-deviation-table tbody').textContent, /未提供/);
  assert.match(ui.get('#report-raw-json').textContent, /"total": 12/);
  pending.A.resolve({ data: { run_id: 'A', result_usable: true } });
  await flush();
  assert.match(ui.get('#report-summary').textContent, /B/);
  await select('C');
  assert.equal(ui.get('#report-content').hidden, false);
  pending.C.reject({ status: 503 });
  await flush();
  assert.equal(ui.get('#report-content').hidden, false);
  assert.equal(ui.get('#report-load').disabled, false);
});

test('reports distinguish empty list from failed list and load every available page', async () => {
  const empty = setup('reports.js', async () => ({ data: { items: [], total: 0 } }));
  const failed = setup('reports.js', async () => { throw { status: 403 }; });
  const paged = setup('reports.js', async (url) => ({ data: {
    items: [{ run_id: url.includes('page=1&') ? 'A' : 'B' }], total: 2,
  } }));
  await flush();
  assert.equal(empty.get('#reports-empty').hidden, false);
  assert.equal(empty.get('#report-picker').hidden, false);
  assert.equal(failed.get('#reports-empty').hidden, true);
  assert.equal(failed.get('#report-selection-state').className, 'permission');
  assert.equal(paged.calls.length, 2);
  assert.equal(paged.get('#report-run-select').children.length, 3);
});

test('refreshing the run list invalidates an in-flight report and resets its load button', async () => {
  const report = deferred();
  const ui = setup('reports.js', (url) => {
    if (url.includes('?')) return Promise.resolve({ data: { items: [{ run_id: 'A' }], total: 1 } });
    return url.endsWith('/report') ? report.promise : Promise.resolve({ data: { run_id: 'A', result_usable: true } });
  });
  await flush();
  ui.get('#report-run-select').value = 'A';
  ui.get('#report-run-select').emit('change');
  ui.get('#report-load').emit('click');
  await flush();
  await ui.get('#reports-refresh').emit('click');
  report.resolve({ data: { run_id: 'A', result_usable: true } });
  await flush();
  assert.equal(ui.get('#report-content').hidden, false);
  assert.equal(ui.get('#report-load').textContent, '查看报告');
  assert.equal(ui.get('#report-load').disabled, false);
});

test('strategy lookup binds review to loaded evidence and rejects stale lookup responses', async () => {
  const pending = {};
  const posts = [];
  const ui = setup('strategy_versions.js', (url, options) => {
    if (url.includes('?')) return Promise.resolve({ data: { items: [
      { strategy_version_id: 'A', name: '策略 A', version: 1, status: 'DRAFT' },
      { strategy_version_id: 'B', name: '策略 B', version: 2, status: 'DRAFT' },
      { strategy_version_id: 'C', name: '策略 C', version: 3, status: 'PUBLISHED' },
    ], total: 3 } });
    const id = url.split('/')[4];
    if (options?.method === 'POST') {
      posts.push({ id, body: options.body });
      return Promise.resolve({ data: { strategy_version_id: id, status: 'PENDING_REVIEW' } });
    }
    pending[id] = deferred(); return pending[id].promise;
  });
  await flush();
  assert.equal(ui.calls.some((call) => call.url.includes('/diff')), false);
  assert.equal(ui.get('#strategy-review-submit').disabled, true);
  function choose(id) {
    ui.get('#strategy-id').value = id; ui.get('#strategy-id').emit('change');
    ui.get('#strategy-lookup').emit('submit');
  }
  choose('A'); choose('B');
  pending.B.resolve({ data: { strategy_version_id: 'B', status: 'DRAFT', changes: {} } });
  await flush();
  pending.A.resolve({ data: { strategy_version_id: 'A', status: 'PUBLISHED', changes: {} } });
  await flush();
  assert.match(ui.get('#strategy-summary').textContent, /B/);
  assert.equal(ui.get('#strategy-review-submit').disabled, false);
  ui.get('#strategy-review').review_note.value = 'checked';
  ui.get('#strategy-review').emit('submit');
  await flush();
  assert.equal(posts[0].id, 'B');
  assert.equal(posts[0].body.decision, 'SUBMIT');
  pending.B.resolve({ data: { strategy_version_id: 'B', status: 'PENDING_REVIEW', changes: {} } });
  await flush();
  assert.equal(ui.get('#strategy-review').decision.value, 'PUBLISH');
  ui.get('#strategy-id').value = 'C'; ui.get('#strategy-id').emit('change');
  ui.get('#strategy-review').emit('submit');
  await flush();
  assert.equal(posts.length, 1);
  assert.equal(ui.get('#strategy-detail').hidden, true);
});

test('ordinary users cannot review and published strategies remain read-only', async () => {
  for (const [roles, status] of [[['USER'], 'DRAFT'], [['ADMIN'], 'PUBLISHED']]) {
    const ui = setup('strategy_versions.js', async (url) => url.includes('?')
      ? { data: { items: [{ strategy_version_id: 'A', name: '策略 A', version: 1, status }], total: 1 } }
      : { data: { strategy_version_id: 'A', status, changes: {} } }, roles);
    await flush();
    ui.get('#strategy-id').value = 'A';
    await ui.get('#strategy-lookup').emit('submit'); await flush();
    assert.equal(ui.get('#strategy-review-submit').disabled, true);
  }
});

test('admin pagination uses server metadata and independent page-size controls', async () => {
  const ui = setup('admin.js', async (url) => {
    const parsed = new URL(url, 'http://test');
    return { data: {
      items: [],
      page: Number(parsed.searchParams.get('page')),
      page_size: Number(parsed.searchParams.get('page_size')),
      total: parsed.pathname.endsWith('/jobs') ? 61 : 42,
    } };
  });
  await flush();
  assert.match(ui.calls.find((call) => call.url.includes('/jobs?')).url, /page=1&page_size=25/);
  assert.match(ui.calls.find((call) => call.url.includes('/audit-events')).url, /page=1&page_size=25/);
  assert.match(ui.get('#jobs-pagination').children[0].textContent, /第 1 \/ 3 页，共 61 条/);
  await ui.get('#jobs-pagination').children[1].children[2].emit('click');
  await flush();
  assert.match(ui.calls.at(-1).url, /\/jobs\?page=2&page_size=25/);
  ui.get('#audit-page-size').value = '10';
  await ui.get('#audit-page-size').emit('change');
  await flush();
  assert.match(ui.calls.at(-1).url, /\/audit-events\?page=1&page_size=10/);
  assert.match(ui.get('#audit-pagination').children[0].textContent, /第 1 \/ 5 页，共 42 条/);
});

test('quality warnings survive empty errors; both categories appear together', async () => {
  for (const errors of [[], ['missing price']]) {
    const ui = setup('data_quality.js', async (url) => url.endsWith('/quality')
      ? { data: { quality_summary: { errors, warnings: ['cross-check mismatch'] } } }
      : { data: { items: [], total: 0 } }, undefined,
    { apiUrl: '/api/v1/data/batches/A/quality' });
    await flush();
    assert.match(ui.get('#quality-issues').textContent, /警告：cross-check mismatch/);
    assert.doesNotMatch(ui.get('#quality-issues').textContent, /未发现/);
    if (errors.length) assert.match(ui.get('#quality-issues').textContent, /错误：missing price/);
  }
});

test('backtest pagination requests the next page and zero results hide the pager', async () => {
  const ui = setup('backtest_detail.js', async () => ({ data: {
    items: [{ run_id: 'A', strategy_version_id: 'S' }], total: 51,
  } }));
  await flush();
  const pager = ui.get('#backtest-pagination');
  assert.equal(pager.children[1].disabled, true);
  await pager.children[3].emit('click');
  assert.match(ui.calls.at(-1).url, /page=2&/);
  const empty = setup('backtest_detail.js', async () => ({ data: { items: [], total: 0 } }));
  await flush();
  assert.equal(empty.get('#backtest-pagination').children.length, 4);
  assert.equal(empty.get('#backtest-empty').hidden, false);
  assert.equal(empty.get('#backtest-table-wrap').hidden, false);
});

test('operation pages expose confirmation gates and stale-plan protections', () => {
  const orderPlans = fs.readFileSync(path.join(__dirname, '../../static/js/order_plans.js'), 'utf8');
  const dailyFlow = fs.readFileSync(path.join(__dirname, '../../static/js/daily_flow.js'), 'utf8');
  const backtestCreate = fs.readFileSync(path.join(__dirname, '../../static/js/backtest_create.js'), 'utf8');
  const dataImport = fs.readFileSync(path.join(__dirname, '../../static/js/data_import.js'), 'utf8');
  const executions = fs.readFileSync(path.join(__dirname, '../../static/js/executions.js'), 'utf8');
  const templates = fs.readFileSync(path.join(__dirname, '../../templates/executions.html'), 'utf8');

  assert.match(orderPlans, /requestNumber !== listRequest/);
  assert.match(orderPlans, /clearDecision\(\)/);
  assert.match(orderPlans, /plan-decision-confirm/);
  assert.match(orderPlans, /history\[replace \? 'replaceState' : 'pushState'\]/);
  assert.match(dailyFlow, /daily-flow-confirm/);
  assert.match(backtestCreate, /backtest-confirm/);
  assert.match(dataImport, /provider-import-confirm/);
  assert.match(dataImport, /data-import-confirm/);
  assert.match(executions, /execution-confirm/);
  assert.match(executions, /Number\(values\.price\) <= 0/);
  assert.match(executions, /remaining_quantity/);
  for (const field of ['commission', 'stamp-tax', 'transfer-fee', 'other-fee']) assert.match(templates, new RegExp(`execution-${field}-error`));
  assert.match(templates, /中国标准时间 UTC\+8/);
});

test('successful plan decision restores controls and keeps the receipt after refreshing plans', async () => {
  const plan = { plan_id: 'P-1', plan_no: 'P-1', version: 1, status: 'PENDING_CONFIRMATION', quantity: 100 };
  const ui = setupOrderPlans(async (url, options) => options?.method === 'POST'
    ? { data: { status: 'CONFIRMED' }, request_id: 'decision-request' }
    : { data: { items: [plan], total: 1, page: 1, page_size: 50 }, request_id: 'list-request' });
  await flush();
  const row = ui.get('#plan-table tbody').children[0];
  await row.children.at(-1).children[0].emit('click');
  ui.form.review_note.value = '已核对计划和风险证据';
  await ui.form.emit('submit');
  assert.equal(ui.calls.filter((call) => call.options?.method === 'POST').length, 0);
  await ui.get('#plan-decision-confirm').emit('click');
  await flush();
  await flush();
  assert.equal(ui.calls.filter((call) => call.options?.method === 'POST').length, 1);
  ui.controls.forEach((control) => assert.equal(control.disabled, false));
  assert.match(ui.get('#plan-state').textContent, /已记录人工决定/);
  assert.equal(ui.get('#plan-state').requestId, 'decision-request');
});

test('failed confirmed operations restore their return-to-edit labels', () => {
  const expectations = [
    ['data_import.js', /submit\.textContent = confirming \? '返回修改' : submit\.dataset\.submitLabel/, /providerSubmit\.textContent = providerConfirming \? '返回修改' : '提交供应商导入'/],
    ['backtest_create.js', /submit\.textContent = confirming \? '返回修改' : submit\.dataset\.submitLabel/],
    ['daily_flow.js', /submit\.textContent = confirming \? '返回修改' : submit\.dataset\.submitLabel/],
    ['executions.js', /execution-submit'\)\.textContent = confirming \? '返回修改'/],
  ];
  expectations.forEach(([file, ...patterns]) => {
    const source = fs.readFileSync(path.join(__dirname, '../../static/js', file), 'utf8');
    patterns.forEach((pattern) => assert.match(source, pattern));
  });
});

test('report page keeps summary tables and folded raw evidence', () => {
  const html = fs.readFileSync(path.join(__dirname, '../../templates/reports.html'), 'utf8');
  const source = fs.readFileSync(path.join(__dirname, '../../static/js/reports.js'), 'utf8');
  assert.match(html, /id="report-metrics-table"/);
  assert.match(html, /id="report-cost-table"/);
  assert.match(html, /id="report-deviation-table"/);
  assert.match(html, /查看原始服务端证据/);
  assert.doesNotMatch(html, /id="report-(?:deviation|cost)"/);
  assert.match(source, /showTable\('#report-metrics-table'/);
  assert.match(source, /report-raw-json/);
});

test('data import template has balanced form tags', () => {
  const html = fs.readFileSync(path.join(__dirname, '../../templates/data_import.html'), 'utf8');
  assert.equal((html.match(/<form\b/g) || []).length, (html.match(/<\/form>/g) || []).length);
});

test('data import tabs switch between provider and local panels', () => {
  const ui = setup('data_import.js', async () => ({
    data: { queue_available: true, providers: { tushare: { enabled: true } } },
  }));

  assert.equal(ui.get('#provider-import-panel').hidden, false);
  assert.equal(ui.get('#local-import-panel').hidden, true);

  ui.get('#local-import-tab').emit('click');

  assert.equal(ui.get('#provider-import-panel').hidden, true);
  assert.equal(ui.get('#local-import-panel').hidden, false);
});
