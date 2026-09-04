# A股短线量化验证系统—API接口设计

| 项目 | 内容 |
|---|---|
| 文档名称 | 06-API接口设计 |
| 版本 | v1.1.0 |
| 状态 | 本地 MVP 路由已实现；外部依赖联调待执行 |
| 最后更新时间 | 2026-09-04 |
| 关联文档 | [需求规格说明书](./02-需求规格说明书.md)、[产品流程与页面说明](./03-产品流程与页面说明.md)、[架构设计](./04-系统架构设计.md)、[数据库设计](./05-数据库设计.md)、[开发规范](./07-开发规范与工程约定.md) |

> API 路径和字段是内部实现契约，首期按 v1.1.0 基线实施；仍允许在开发阶段通过版本化变更调整。基础路径为 `/api/v1`，传输 JSON/UTF-8，金额和价格以字符串返回以避免精度丢失；系统不提供自动下单 API。

> 实现核对（2026-09-04）：本地代码已挂载认证、数据导入/查询、策略、回测、日报/计划/人工成交、管理员任务/审计和页面路由；持久化开发路径使用 SQLite 或配置的 PostgreSQL。本文记录的是接口契约，不把本地测试替代为 PostgreSQL/Redis/Docker 联调通过；`broker/order-submit`、资金划转和自动恢复路由仍不存在。

## 0. v1.1.0 配置决策

- 数据导入接口只接收合法授权的 CSV/Parquet 文件，不内置未授权供应商抓取器。
- 回测默认起始日为 2016-01-01，主基准为 `000300.SH`，次基准为 `000001.SH`，并计算股票池等权基准。
- 成交模型为 `NEXT_OPEN_ADJUSTED`：买入开盘价上浮 0.20%，卖出开盘价下调 0.20%；回测部分成交模式为 `FULL_OR_NONE`。
- 计划确认截止下一交易日 09:25；8% 回撤恢复需要二次确认，接口不得提供自动恢复或自动下单动作。

## 1. 认证、权限与通用约定

- 认证：本地账号 + `Authorization: Bearer <access_token>`；访问 Token 有效期 30 分钟，刷新 Token 有效期 7 天，注销、停用账号或管理员撤销时立即失效。
- 所有请求带 `X-Request-Id`；写操作带 `Idempotency-Key`。服务端将请求号和幂等键写入审计。
- RBAC：`USER` 可研究/回测/录入；`REVIEWER` 可发布策略/确认计划；`ADMIN` 可配置数据源、用户和任务。
- 日期使用 `YYYY-MM-DD`，时间使用 ISO 8601；分页使用 `page`（从 1 开始）、`page_size`（默认 50，上限 200）。
- 列表返回 `items/page/page_size/total`；详情返回 `data/request_id`。
- `planned_order` 只代表计划；不存在 `broker/order-submit`、自动委托或资金划转接口。

## 2. 统一返回和错误码

成功结构：

```json
{
  "data": {},
  "request_id": "req_20260903_0001"
}
```

错误结构：

```json
{
  "error": {
    "code": "BT_DATA_UNAVAILABLE",
    "message": "数据批次不可用，回测已阻断",
    "details": [{"field": "data_batch_id", "reason": "缺少交易日"}]
  },
  "request_id": "req_20260903_0002"
}
```

| HTTP | 错误码 | 含义 |
|---:|---|---|
| 400 | `VALIDATION_ERROR` | 字段格式或业务边界错误 |
| 401 | `AUTH_REQUIRED` | 未认证或 Token 失效 |
| 403 | `FORBIDDEN` | 无权限 |
| 404 | `NOT_FOUND` | 资源不存在或不可见 |
| 409 | `IDEMPOTENCY_CONFLICT` | 同幂等键参数不一致/重复写入 |
| 409 | `STATE_CONFLICT` | 状态迁移不允许 |
| 422 | `RULE_VIOLATION` | 交易规则、整数股或风险约束不满足 |
| 422 | `BT_DATA_UNAVAILABLE` | 数据/成本/规则不完整，运行不可用 |
| 429 | `RATE_LIMITED` | 请求过频 |
| 500 | `INTERNAL_ERROR` | 未分类服务错误 |
| 503 | `DEPENDENCY_UNAVAILABLE` | 数据源、队列或存储不可用 |

## 3. 接口列表

| 编号 | 方法与路径 | 权限 | 用途 |
|---|---|---|---|
| API-DATA-001 | `POST /data/batches` | USER/ADMIN | 创建导入批次 |
| API-DATA-004 | `POST /data/imports` | USER/ADMIN | 导入授权 CSV/Parquet 并执行质量闸门 |
| API-DATA-002 | `GET /data/batches/{id}/quality` | USER | 查看质量报告 |
| API-DATA-003 | `GET /securities/pool?trade_date=` | USER | 查看历史股票池 |
| API-DATA-005 | `GET /data/batches`、`GET /data/bars` | USER | 分页查看批次和日线 |
| API-STRAT-001 | `POST /strategies` | USER | 创建策略草稿 |
| API-STRAT-002 | `POST /strategies/{id}/submit-review` | REVIEWER/ADMIN | 提交/发布版本 |
| API-STRAT-003 | `GET /strategies/{id}/diff` | USER | 查看版本差异 |
| API-BT-001 | `POST /backtests` | USER | 创建回测运行 |
| API-BT-005 | `GET /backtests` | USER | 分页查看回测运行 |
| API-BT-002 | `GET /backtests/{run_id}` | USER | 查看运行与阶段 |
| API-BT-003 | `GET /backtests/{run_id}/trades` | USER | 分页查看交易 |
| API-BT-004 | `GET /backtests/{run_id}/report` | USER | 查看指标/报告 |
| API-BT-006 | `POST /backtests/{run_id}/execute` | USER | 执行已持久化回测 |
| API-OPS-000 | `POST /daily-flows` | USER | 运行幂等的日终流程 |
| API-OPS-001 | `GET /daily-reports/{date}` | USER | 查看日报 |
| API-OPS-002 | `GET /order-plans?execution_date=` | USER | 查看计划 |
| API-OPS-003 | `POST /order-plans/{id}/confirm` | REVIEWER/ADMIN | 人工确认/跳过 |
| API-OPS-004 | `POST /executions` | USER | 录入实际成交 |
| API-OPS-005 | `GET /accounts/{id}/snapshots` | USER | 查看账户账本 |
| API-OPS-006 | `GET /reports/{id}/export` | USER | 导出文件 |
| API-OPS-007 | `GET /exports/{id}` | USER | 查询异步导出状态 |
| API-ADMIN-001 | `GET /jobs`、`POST /jobs/{id}/retry` | ADMIN | 查看/重试任务 |
| API-ADMIN-002 | `GET /audit-events` | REVIEWER/ADMIN | 查询审计 |

## 4. 关键请求与返回

### API-BT-001 创建回测

请求：`POST /api/v1/backtests`

```json
{
  "data_batch_id":"db_001",
  "strategy_version_id":"strat_001_v3",
  "cost_config_id":"cost_2026_user_01",
  "rule_config_id":"rule_mainboard_2026_v1",
  "start_date":"2021-01-01",
  "end_date":"2025-12-31",
  "train_end":"2023-12-31",
  "valid_end":"2024-12-31",
  "oos_start":"2025-01-01",
  "benchmark_symbol":"000300",
  "initial_equity":"20000.00",
  "mode":"BACKTEST"
}
```

返回 `202 Accepted`：

```json
{"data":{"run_id":"run_20260903_0001","status":"QUEUED","result_usable":false},"request_id":"req_1"}
```

预检失败返回 422，并指出数据缺失、费率未配置或时间切分不合法；同一 `Idempotency-Key` 重试返回同一 `run_id`。

### API-OPS-003 人工确认计划

请求：`POST /api/v1/order-plans/plan_001/confirm`

```json
{"decision":"CONFIRM","review_note":"已人工核对现金、持仓与风险提示","expected_version":3}
```

返回：

```json
{"data":{"plan_no":"plan_001","status":"CONFIRMED","execution_date":"2026-09-04"},"request_id":"req_2"}
```

若回撤已经达到停止线、计划日期已过或版本不匹配，返回 `STATE_CONFLICT`/`RULE_VIOLATION`，不改变计划。

### API-OPS-004 录入实际成交

请求：`POST /api/v1/executions`

```json
{
  "plan_id":"plan_001",
  "execution_type":"MANUAL_ENTRY",
  "executed_at":"2026-09-04T09:42:10+08:00",
  "quantity":100,
  "price":"12.35",
  "commission":"5.00",
  "stamp_tax":"0.00",
  "transfer_fee":"0.02",
  "other_fee":"0.00",
  "unfilled_quantity":0,
  "note":"人工确认后按成交回报录入"
}
```

服务端校验计划状态、数量、交易日、费用非负和幂等键；写入 `execution_record`、`ledger_entry`、差异摘要和审计事件，不修改原计划。

### API-DATA-003 查看股票池

`GET /api/v1/securities/pool?trade_date=2026-09-03&page=1&page_size=50&status=IN_POOL`

返回字段：`symbol/exchange/board/in_pool/exclusion_reasons/listed_trade_days/amount_median_20/status_as_of/source_batch_id`。必须显示日期和历史状态来源。

### API-OPS-001 查看日报

`GET /api/v1/daily-reports/2026-09-03`

返回：`report_date/run_id/data_quality/market_switch/account/holdings/candidates/order_plans/risk_state/actual_execution_input/notice`。候选字段必须含条件、分数和风险标签；`notice` 固定包含“不构成投资建议、不承诺收益、不自动下单”。

## 5. 过滤、排序与分页

列表接口只允许白名单字段排序，例如 `trade_date asc`、`score desc`、`created_at desc`；服务端限制时间范围和最大页大小。导出接口异步生成 `export_id`，通过 `GET /exports/{id}` 查询状态，不在同步请求中阻塞大回测。

## 6. 幂等性与并发

写操作使用客户端 `Idempotency-Key`，服务端保存哈希、操作者、结果，保留期固定为 7 天。回测幂等键建议为 `hash(data_version,strategy_version,cost_version,rule_version,time_range,mode)`；计划生成键为 `strategy_version+data_version+as_of_date+account_id`；成交录入键为 `plan_id+executed_at+quantity+price+source`。

状态变更采用乐观锁 `expected_version`；冲突返回 409。账本写入使用事务，重复请求不得重复扣现金或新增持仓。

## 7. 安全与审计

- 所有接口默认鉴权；错误不泄露隐藏资源是否存在。
- 仅管理员能修改数据源、规则、费用和任务；费用/规则修改必须新增版本。
- 所有发布、确认、跳过、成交录入、更正、重试和导出写 `audit_event`。
- API 日志不记录 Token、密钥、完整个人信息；导出使用已认证的短期下载凭证，有效期 15 分钟且只能下载一次。

## 8. API 与数据库映射

| 接口族 | 主要表 |
|---|---|
| DATA | `data_batch`、`daily_bar`、`security_status_history`、`trade_calendar` |
| STRAT | `strategy_version`、`signal_snapshot` |
| BT | `backtest_run`、`run_stage`、`portfolio_snapshot`、`performance_metric`、`report_artifact` |
| OPS | `daily_report`、`order_plan`、`execution_record`、`ledger_entry` |
| ADMIN | `job_run`、`audit_event`、`system_alert` |

## 9. 固定实施约束

1. API 默认只监听本机回环地址；如需局域网访问，必须新增部署变更和访问控制，不开放公网。
2. 使用本地账号和 RBAC；单用户账号拥有 `USER/REVIEWER/ADMIN`，审核动作仍独立审计。
3. 运行环境使用 PostgreSQL 16；异步任务使用 Redis 7/RQ；幂等结果保留 7 天。
4. 成交回报只允许网页表单或人工准备的 CSV 导入；禁止券商委托、撤单、资金划转和自动恢复接口。
