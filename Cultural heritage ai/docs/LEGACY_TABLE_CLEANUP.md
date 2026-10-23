# 两张旧知识整理表的代码清理记录

日期：2026-10-23。本记录对应用户在数据库审计后明确授权的代码清理；不把此前审计的“候选清理”改写成当时已经删除。

## 1. 已完成的代码与 ORM 清理

全局复核 `app/`、`tests/`、`scripts/`、`evaluation/`、SQL、迁移和文档后，两个旧模型只有注册和元数据测试依赖，没有生产 Repository、Router、消费者或动态 SQL 业务入口。

删除的文件：

- `app/modules/hcp_mad/legacy_knowledge_curation/domain/models.py`：移除 `CrowdTaskParticipantTable`、`TaskKnowledgeIngestionTable` 及仅供它们使用的两个枚举、约束和 import。
- `app/modules/hcp_mad/legacy_knowledge_curation/domain/__init__.py`。
- `app/modules/hcp_mad/legacy_knowledge_curation/__init__.py`。

该目录没有其他有效实现，整个遗留模块已移除。唯一生产代码修改是 `app/db/session.py` 中删除旧模型注册 import；`init_db()` 仍只注册映射，不连接数据库、不创建或删除表。

其他变更：

| 文件 | 修改原因 |
| --- | --- |
| `tests/test_application_startup.py` | 新进程验证公共 3 + HCP 5 + RAG 9 = 17 表，应用导入不会重新注册旧表；外键可解析，启动不执行 DDL，路由数量不变 |
| `tests/test_legacy_table_cleanup.py`（新增） | 正式 SQL 与历史导出的其余 17 表逐字一致、外键排序、MySQL 方言编译、临时 SQLite 建表，以及生产引用清零检查 |
| `tests/hcp_mad/test_architecture_governance.py` | 从架构检查的持久化层例外路径中移除已删除目录，没有放宽架构规则 |
| `tests/hcp_mad/test_failure_diagnostics.py` | 原真实 HCP 工具执行全链增加无旧表/保留旧物理表两个参数场景，监听 SQL 禁止访问旧表 |
| `tests/rag_task/test_crowd_auto_recovery.py` | 原三人 HTTP 提交→真实 Loop/Observer/Provider→聚合结果→RAG 入库测试增加相同两种场景，验证旧表哨兵数据不变 |
| `sql/mysql_schema.sql`（新增） | 当前正式 17 表新库入口，按物理外键依赖排序，不包含旧表、数据库创建、数据迁移或删除 |
| `README.md` | 指明当前与历史 SQL 入口，避免旧导出重新创建旧表 |
| `docs/DATABASE_TABLE_AUDIT.md` | 追加后续清理记录，保留原始审计事实 |
| `docs/AGENT_EXECUTION_SPLIT.md` | 追加后续状态，保留原迁移历史 |
| `docs/LEGACY_TABLE_CLEANUP.md`（新增） | 本次清单、验证边界及人工数据库清理说明 |

执行前后 SHA-256 对比确认：除删除旧模块和移除注册行外，其余 240 个应用 Python 文件未改变；其他 17 张表的 ORM 源码未改变。未修改 HCP 状态机、P/T 事务、幂等、恢复、终局逻辑，也未修改公共 Runner、Provider、ToolExecutor、RAG Pipeline 或环境变量。

## 2. 当前 17 张有效表

| 业务域 | 表 |
| --- | --- |
| 公共基础（3） | `accounts`、`ai_agents`、`heritage_sites` |
| HCP-MAD（5） | `tasks`、`answers`、`task_contexts`、`task_context_items`、`agent_executions` |
| RAG Task（9） | `rag_tasks`、`rag_task_answers`、`rag_task_contexts`、`rag_task_context_items`、`rag_task_agent_executions`、`rag_task_participants`、`rag_task_results`、`rag_task_knowledge_ingestions`、`rag_task_processing_jobs` |

旧 `crowd_task_participants` 和 `task_knowledge_ingestions` 不再进入 `Base.metadata.tables`。不要将新表 `rag_task_participants` / `rag_task_knowledge_ingestions` 误删。

## 3. 正式 SQL 与历史文件

现有 README 将 `sql/createtable.sql` 标记为用户原始结构导出，因此保留其字节内容，不删除历史 CREATE。新增的 **`sql/mysql_schema.sql` 才是本项目当前正式新库结构入口**。它只含 `SET NAMES` 和 17 个 CREATE TABLE 块；每个块与原始导出对应块逐字一致，只调整表间顺序，保证所引用父表先创建。不关闭外键检查，不对已有库做隐式升级。

SQL 文本结构、表集合、括号配对、外键依赖及 ORM 的 MySQL 方言编译在自动测试中检查。本轮未在真实 MySQL 执行脚本，因此不声称完成 MySQL 服务器端语法验收。审计已发现的 `answers.execution_id` 物理外键缺失等历史差异维持原样；本轮不改变其余 17 表结构。

保留的历史文件：

- `sql/createtable.sql`：19 表原始导出，SHA-256 与清理前相同，仅供审计比较，不用于当前新部署。
- `migrations/20260817_drop_accounts_role.sql`、`migrations/20261009_add_ai_agent_provider_fields.sql`：既有历史迁移，原样保留。
- `docs/DATABASE_TABLE_AUDIT.md` 原有章节、`docs/AGENT_EXECUTION_SPLIT.md` 原有迁移记录，以及众包文档中“不复用旧表”的说明：属于历史证据或边界说明，不是生产依赖。
- 工作区外 `../deployment/sql/user_export_reference.sql` 和 `../deployment/sql/mysql_schema.sql` 没有修改；后者仍包含清理前的 19 表，不应直接用于本次版本的新部署。若继续使用父目录部署工具，应先仅执行 `render`、审查生成结果并同步其发布材料。不要在已有业务库运行 `init`；该工具本身也拒绝非空库。

没有创建自动 DROP 迁移。旧表名在测试中只用于排除断言、SQL 访问禁令及模拟过渡期的测试表；生产 Python、现行 SQL、脚本和评估代码没有旧表依赖。

## 4. 验证与边界

新增 3 个测试函数，并将 2 个现有集成用例各扩展为 2 个参数场景，共增加 5 个测试实例。没有删除业务测试。

两类隔离集成测试均使用真实 Facade、Loop、Observer、Provider 解析和持久化逻辑，仅替换外部模型网络、Embedding 和向量后端。众包使用测试 lifespan 的离线消费者；没有启动真实消费者。SQL 监听同时防止跨业务域和旧表访问。

过渡兼容测试的额外两表是仅含主键和历史哨兵行的临时 SQLite 测试表，用于证明额外物理表不会被访问、注册、清空或影响完整执行；不声称模拟旧表全部 MySQL 结构，也不代替真实 MySQL 外键和行锁验收。普通测试数据库仅创建有效 17 表。

以下均为本轮实际执行结果；完整测试没有全部通过，剩余失败单独列明。

| 检查 | 结果 |
| --- | --- |
| 针对性 pytest（注册、架构、SQL、HCP、众包两种过渡状态） | 34 passed |
| 完整 pytest | **1187 passed、3 failed，479.94 秒**；1190 项均执行完成，3 处失败是原已缺失的两份历史增量 SQL |
| 分域回归（包含在完整 pytest 中） | HCP 535 passed；RAG 检索 81 passed；账号 71 passed；评估 12 passed；RAG Task 230 passed / 2 failed；根目录公共回归 258 passed / 1 failed |
| 全项目 Ruff | `app tests scripts evaluation` 全部通过 |
| 本轮核心 mypy | `app/db/session.py`、启动测试、新增清理测试、众包恢复测试共 4 文件通过 |
| 扩大 mypy（6 个变更 Python 文件） | 两个旧 HCP 测试文件的 11 处既存类型问题；用撤销本轮测试编辑后的临时 shadow-file 复核，同样 11 处，没有新增错误 |
| Python 语法 | 所有应用 Python 和本轮 5 个测试文件，共 246 个文件 AST 解析及内存 `compile()` 通过；不生成字节码 |
| 路由与生产代码保护 | 精确路径/方法映射仍为 22 路径、24 操作；其余 240 应用文件哈希不变 |
| 真 MySQL / 模型 / Milvus | 本轮未连接、未执行，不能报告线上或 MySQL 并发验收通过 |

实际命令（PowerShell，项目根目录，使用离线测试替身；不启动正常应用）：

```powershell
.\.venv\Scripts\python.exe -B -u -m pytest -q -p no:cacheprovider --basetemp=.pytest_tmp/legacy-table-cleanup-20261023/focused2 tests/test_legacy_table_cleanup.py tests/test_application_startup.py tests/hcp_mad/test_architecture_governance.py tests/hcp_mad/test_failure_diagnostics.py::test_full_hcp_tool_pipeline_never_reads_or_writes_rag_task_tables tests/rag_task/test_crowd_auto_recovery.py::test_third_http_submission_lifespan_consumes_and_saves_real_execution_result_and_ingestion
.\.venv\Scripts\python.exe -B -u -m pytest tests -q -p no:cacheprovider --basetemp=.pytest_tmp/legacy-table-cleanup-20261023/full1
.\.venv\Scripts\python.exe -B -m ruff check app tests scripts evaluation
.\.venv\Scripts\python.exe -B -m mypy app/db/session.py tests/test_application_startup.py tests/test_legacy_table_cleanup.py tests/rag_task/test_crowd_auto_recovery.py
.\.venv\Scripts\python.exe -B -m mypy app/db/session.py tests/test_application_startup.py tests/test_legacy_table_cleanup.py tests/hcp_mad/test_architecture_governance.py tests/hcp_mad/test_failure_diagnostics.py tests/rag_task/test_crowd_auto_recovery.py
```

第一次受限环境中的 pytest 因 Windows 本地异步通信限制挂起，已结束该专属测试进程；以上通过结果来自其后实际完成的隔离运行。日志保存在 `.pytest_tmp/legacy-table-cleanup-20261023/`。

完整 pytest 剩余问题（本轮开始时文件即不存在，README 亦已记录；未删除或放宽测试）：

| 测试 | 实际失败原因 |
| --- | --- |
| `tests/rag_task/test_crowd_schema.py::test_new_sql_matches_all_four_orm_tables_and_existing_answer_alter` | 缺少 `sql/20261022_add_rag_task_crowd_workflow.sql`，`FileNotFoundError` |
| `tests/rag_task/test_crowd_schema.py::test_published_stage_one_migration_is_unchanged` | 缺少 `sql/20261022_add_rag_task_tables.sql`，`FileNotFoundError` |
| `tests/test_execution_architecture.py::test_incremental_sql_matches_all_new_orm_columns_indexes_constraints_and_defaults` | 同样缺少 `sql/20261022_add_rag_task_tables.sql` |

这些是历史发布材料完整性问题，不能用当前 17 表建库脚本替换或编造哈希来“修好”测试。如需后续恢复，应从可信历史提交/备份找回两份原文，不在现有业务库重复执行旧迁移。扩大 mypy 的既存错误集中在 `test_architecture_governance.py` 的容器类型/测试替身类型，以及 `test_failure_diagnostics.py` 的可空局部变量和基础设施替身类型；本轮不顺带修复。

## 5. 用户手动清理 MySQL

**本轮真实 MySQL 表尚未删除，也没有重新查询数据库。** 此前审计的两表各 0 行和无可见入向外键，仅是当时快照，不代替删除前的再次检查。

建议顺序：

1. 部署当前代码，停用仍注册旧模型的旧实例、外部脚本和报表；确认没有其他系统依赖旧表。
2. 维护窗口内先做完整库备份，并单独备份两张旧表的建表语句及数据；将备份恢复到隔离库演练。不要因为此前 0 行而省略备份。
3. 用足够权限检查入向外键、视图、触发器、事件和存储过程，并重新核对数据量。以下仅为供用户手动执行的只读检查：

```sql
SELECT DATABASE();
SHOW CREATE TABLE crowd_task_participants;
SHOW CREATE TABLE task_knowledge_ingestions;
SELECT COUNT(*) FROM crowd_task_participants;
SELECT COUNT(*) FROM task_knowledge_ingestions;
SELECT TABLE_NAME, COLUMN_NAME, CONSTRAINT_NAME, REFERENCED_TABLE_NAME
FROM information_schema.KEY_COLUMN_USAGE
WHERE REFERENCED_TABLE_SCHEMA = DATABASE()
  AND REFERENCED_TABLE_NAME IN ('crowd_task_participants', 'task_knowledge_ingestions');
SELECT TABLE_NAME
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'
ORDER BY TABLE_NAME;
```

4. 若其他环境有历史行，先明确归档或迁移规则；不要直接把旧 `tasks.id` 当成 `rag_tasks.id`，不需要为了本轮代码升级迁移这两表数据。
5. 用户人工删除的准确名称只有 `crowd_task_participants`、`task_knowledge_ingestions`。此前审计中两表彼此无外键：前者向 `tasks/answers/accounts` 建外键，后者向 `tasks` 建外键，父表保留，因而两旧表之间没有强制删除顺序。如复查出现新的入向依赖，先停止删除并处理依赖；不要关闭外键检查强删。
6. 删除后再次查询 BASE TABLE 清单，与第 2 节 17 表集合逐一比较；若库中另有其他系统表，不要为了凑 17 张而删除它们。
7. 重跑注册、登录、HCP 创建/Worker/Judge/Debate/恢复和众包三人提交/聚合/入库/重试回归；真实 MySQL 在隔离副本上验证锁和并发，再进行人工服务冒烟。

代码允许 ORM 17 表而数据库暂留 19 表，不要求立即删表、不要求重建数据库。`init_db()` 没有比较物理表全集必须相等的逻辑。

回滚：保留本次代码变更前的版本与数据库备份。若人工删表后需恢复旧版本，应先从备份恢复旧表结构和原数据，再回滚旧应用；不能仅依赖 ORM 自动建表。其他 17 张表不需要回滚或重建。
