# AGENTS.md

## 项目定位

这是一个面向沪深 A 股的研究、回测、日终验证和人工确认系统，不是自动交易系统。系统可以生成信号、目标仓位、订单计划、回测成交和人工录入的实际成交，但不能连接券商，也不能自动下单、撤单、补单、转账或自动恢复风险状态。

当前代码是 Python 3.12 模块化单体：FastAPI + SQLAlchemy/Alembic + SQLite（本地开发/测试）或 PostgreSQL（simulation/production）+ Redis/RQ（异步任务）+ Parquet/manifest（不可变研究产物）。前端是 Jinja2 模板加 `static/js`，没有独立前端构建工程。

## 先读什么

代码是实现事实；编号文档描述产品和架构基线，`docs/superpowers/specs`、`docs/superpowers/plans` 是设计意图，必须用测试和当前实现确认其完成度。

- `README.md`：当前本地验证范围、环境变量、日终/回测命令和已知基础设施限制。
- `04-系统架构设计.md`：模块边界和不可违反的时间隔离规则。
- `05-数据库设计.md`、`06-API接口设计.md`：持久化模型和接口契约。
- `07-开发规范与工程约定.md`：命名、版本化和提交约定。
- `08-测试计划与测试用例.md`：验收范围和 P0/P1 风险。
- `09-发布部署与回滚方案.md`、`10-运维与应急预案.md`：部署、恢复和故障处置。
- `docs/runbooks/operations.md`、`docs/runbooks/deployment.md`：当前可执行运维步骤。

## 代码地图

```text
app/api + templates/static
        │ HTTP / 页面 / 鉴权 / RBAC / 幂等键
app/application
        │ 编排：导入、日终流程、回测、iFinD/Tushare 批次
app/domain
        │ 纯业务：数据质量/股票池、特征/策略、风控、成交模拟、账本、指标
app/infrastructure
        │ 数据库/仓储、Parquet、供应商 HTTP 客户端、raw 归档
app/jobs
        │ RQ 队列、稳定任务键、可重放的任务包装器
scripts
        │ 本地导入、日终、回测、worker、备份、恢复检查、质量检查
```

关键入口：

- `app/main.py:create_app`：应用工厂；生产/开发使用 SQLAlchemy 仓储，`APP_ENV=test` 使用内存替身。
- `app/api/v1/data.py`：批次导入、质量、股票池、日线查询。
- `app/api/v1/strategies.py`：策略创建、送审和版本差异。
- `app/api/v1/backtests.py`：回测创建、执行、交易和报告查询。
- `app/api/v1/operations.py`：日终流程、日报、计划确认、人工成交录入、导出。
- `app/api/v1/admin.py`：任务管理和受保护的数据源导入队列；iFinD/Tushare 接口要求 `ADMIN`。
- `app/application/daily_flow.py`、`backtest_service.py`：应用编排和数据门禁。
- `app/domain/backtest/runner.py`、`strategy/`、`risk/`、`execution/`、`portfolio/`：可复用的研究引擎。
- `app/infrastructure/repositories/research.py`：研究对象、批次和运行结果的持久化边界；`runtime.py`：账号、审计、幂等和任务运行记录。
- `app/infrastructure/storage/parquet_store.py`：`raw`、`standardized`、`reports` 文件产物与 SHA-256 manifest。
- `app/infrastructure/tushare/`、`ifind/`、`akshare/`：供应商契约、HTTP 客户端、原始响应归档和验证适配器。
- `alembic/versions/`：数据库迁移；不要直接编辑已应用迁移来修历史结构。

## 核心数据流

```text
授权文件或供应商响应
  → raw 归档/脱敏/哈希
  → 标准化行情与元数据
  → 质量门禁和可用批次
  → 历史股票池（point-in-time）
  → 特征/信号
  → 风控缩减或拒绝仓位
  → T+1 成交模拟 / 订单计划
  → 账本、日报、导出
  → 人工确认与实际成交录入（两类事件分开）
```

## 量化正确性红线

修改策略、数据仓储、回测、日终流程或供应商映射时，必须保留以下不变量：

1. 信号只读取 T 日及以前、且在 `information_cutoff_at` 前可得的数据；T 日收盘产生信号，最早 T+1 执行。禁止未来函数、幸存者偏差和用当前状态覆盖历史状态。
2. 数据批次、策略、规则、成本和运行快照必须绑定版本与内容哈希。历史运行不可被“修正覆盖”；纠正应生成新批次/新版本/新运行。
3. 质量错误或关键字段缺失必须 fail closed，不能用前值、当前值或静默填补来发布可用批次。下游只接受可用且满足截止时间的数据。
4. 股票池负责资格筛选；策略负责特征、资格和分数；风控只能缩减/拒绝仓位，不得改写信号分数；成交模拟只判断可成交性和成本，不创建信号。
5. 默认交易边界是 A 股 100 股整数手、`NEXT_OPEN_ADJUSTED`、`FULL_OR_NONE`；停牌、涨跌停、缺价、越界价格和现金不足必须保留明确未成交原因。
6. 计划确认、实际成交、账本更新和审计事件必须是可追踪的独立记录；人工成交录入不能伪造模拟成交，也不能绕过审核角色。
7. Tushare 是当前主推的 HTTP 行情主源；iFinD 兼容导入路径仍保留其既有批次语义；AKShare 只用于归档证据和非阻断交叉验证。AKShare warning 不得替换、修复或降级主批次；主源必需数据不完整时批次必须 `UNAVAILABLE`。
8. 系统永远不引入 broker client、order submit、cancel、repair 或自动切源路径。涉及交易边界的改动必须同时检查 `tests/security/test_no_broker_path.py`。

## 数据源、凭证与安全

- 供应商 Token/refresh token 只从环境或部署密钥读取，不能放进任务参数、URL、数据库业务字段、manifest、审计或普通日志。
- HTTP 客户端应把超时、429、5xx归为可分类的 `DependencyError` 并有限重试；权限、字段契约和数据质量错误不得盲目重试或泄漏供应商异常。
- 原始响应必须经过脱敏后再归档，并记录请求/响应哈希和 manifest；不要把真实行情或供应商响应伪装成测试数据。
- `.env`、数据库文件、运行产物、缓存和供应商凭证不提交；只更新 `.env.example` 中的非秘密配置说明。
- 所有写 API 需要 `Idempotency-Key`。任务键必须稳定，例如 `data-import:<YYYY-MM-DD>:tushare-pilot`；重复完成任务应重放结果，不重复计划、费用、成交或文件。
- 角色边界：`USER` 读写自身研究/执行录入，`REVIEWER` 可审核计划，`ADMIN` 执行管理和数据导入。账号、会话、刷新令牌、审计和幂等记录使用持久化 runtime store。

## 常用本地流程

从仓库根目录运行，并优先使用共享虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
$env:APP_ENV = "development"
$env:AUTH_SECRET_KEY = "development-only-secret-change-me"
$env:DATABASE_URL = "sqlite:///money-mvp.db"
```

开发环境可按 `DEVELOPMENT_USERNAME` / `DEVELOPMENT_PASSWORD` 覆盖默认本地账号；该账号只允许 development，不能带入 test/simulation/production。

常用检查：

```powershell
python -m pytest -q --basetemp .pytest-tmp-local
python -m ruff check app tests scripts
python -m mypy app
python -m ruff format --check app tests scripts
python scripts/run_quality_checks.py
alembic upgrade head
alembic check
git diff --check
```

Windows 环境如果 pytest 扫描系统临时目录遇到 ACL 错误，使用仓库内或明确的临时目录传入 `--basetemp`，不要把权限问题误判为测试失败。`ruff format --check` 只对自己修改的文件修复；脏工作区中不要未经确认执行全仓格式化。

本地研究命令需要先准备质量通过的批次和已发布策略：

```powershell
python scripts/import_data.py <authorized.csv-or-parquet>
python scripts/run_daily.py --dry-run
python scripts/run_daily.py --business-date <YYYY-MM-DD>
python scripts/run_backtest.py --dry-run
python scripts/run_backtest.py --business-date <YYYY-MM-DD>
```

日终/回测的 owner、batch、strategy-version、日期分区和信息截止时间通过环境变量传入，具体字段以 `README.md` 和脚本参数为准。回测必须满足 `start <= train_end < valid_end < oos_start <= end`。

供应商导入通过受保护管理 API/队列执行，不要直接从 worker 绕过幂等和审计边界：

```text
POST /api/v1/admin/data-imports/tushare/{business_date}?scope=pilot|full
POST /api/v1/admin/data-imports/ifind/{business_date}?scope=pilot|full
```

默认先跑四只 pilot 标的；Tushare `full` 只有在 pilot 证据和权限被人工批准后才可启用。调度时点为 Asia/Shanghai 18:30，完整历史回填按交易日逐日执行。

## 数据库、文件与部署

- `alembic.ini` 当前默认指向本地 `data.db`；正式数据库 URL 由环境配置提供。迁移前备份，迁移后执行结构/约束检查；优先新增向前兼容迁移。
- 研究数据和报告写入 `DATA_ROOT`，按 provider/layer/version 组织；manifest 的路径、格式、字节数、行数和 SHA-256 是恢复与复现证据。
- `scripts/backup.py` 创建备份及文件级 manifest；`scripts/restore_check.py` 只做恢复前验证，不执行覆盖恢复。
- 生产/模拟目标依赖 PostgreSQL 16、Redis 7、RQ worker 和 Docker Compose。当前机器的静态校验使用 `docker-compose config`；Docker daemon/容器链路是否可用要单独验证。
- 发布回滚优先切回上一镜像并保留不可变数据卷；不要 `git reset --hard`、回滚迁移、重写历史 manifest 或删除历史运行数据。

## 测试策略

按变更范围选择测试，但提交前至少跑完整 pytest、Ruff lint 和 mypy。重点测试层次：

- `tests/unit/`：策略/指标/配置/客户端/契约/任务等纯逻辑。
- `tests/api/`：认证、RBAC、幂等、页面和 API 契约。
- `tests/integration/`：数据库、迁移、持久化导入、日终、回测、供应商夹具和恢复证据。
- `tests/e2e/`：导入到报告的完整人工流程。
- `tests/security/`：密钥脱敏和无券商路径，任何边界改动都要回归。
- `tests/performance/`：20 万条日线输入的耗时/内存实测；测量结果是证据，不等于自动满足性能目标。

优先复用 `tests/fixtures/` 的授权合成/录制数据。外部网络、真实 Token、真实供应商调用和真实恢复覆盖不是普通测试步骤；供应商客户端应使用可注入 transport/recording。

## 工作区协作规则

当前工作区不是干净基线，存在未提交的 iFinD/Tushare/AKShare 接入及相关配置、仓储、API、模板、测试和设计文档变更。它们属于现有工作，不得清理、回退、整体暂存或覆盖。开始改动前查看 `git status --short` 和目标文件 diff；完成后只用显式路径暂存自己负责的文件。

不要使用 `git reset --hard`、`git checkout --`、全仓 `git add .` 或删除未知临时目录。不要改 `.env`、数据库和用户生成产物来“让测试通过”。如果当前设计计划与实现不一致，先记录差异并用测试/代码确认，再做最小范围修复。

提交前报告：改动文件、量化正确性影响、测试命令及结果、尚未验证的 PostgreSQL/Redis/Docker 或真实供应商依赖。任何无法证明无未来函数、无越权、无自动委托的改动，不得宣称完成。
