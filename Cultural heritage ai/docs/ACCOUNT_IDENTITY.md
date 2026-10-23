# 统一账号与认证说明

> 2026-10-23：当前公共/个人GET列表、安全DTO与旧API删除情况见 [RAG众包交付报告](RAG_CROWD_DELIVERY.md)。以下旧路由/权限描述属于历史实施记录，不代表当前可调用API。

## 当前账号契约

Account、AccountTable、RegisterRequest、RegisterResponse、LoginRequest、LoginResponse.account 和 CurrentUser 均不包含账号 role。当前工作区原本已经移除了这些字段及 require_role；本轮补齐请求、Token 签发、资源访问和真实 API 测试的边界，没有重新实现账号模型。

注册与登录示例：

```http
POST /users/register
Content-Type: application/json

{"username":"alice","password":"sharedPass123"}
```

```http
POST /users/login
Content-Type: application/json

{"username":"alice","password":"sharedPass123"}
```

这两个接口均拒绝 role、user_id 等额外请求字段，返回 422。注册返回 user_id/username；登录返回 access_token、token_type 和公开账号 id/username/score，不返回密码哈希。

登录校验用户名和密码后签发包含 sub、exp、唯一 jti 的 JWT。sub 使用数据库账号 ID；Redis 会话仍按 token:{账号ID} 存储，保持每个账号只有一个当前 Token 的原有语义。认证继续验证签名、过期时间、登出黑名单、当前会话与账号存在性。

签发函数显式移除调用方传入的 role。历史 Token 如果包含 role，只要签名、有效期、当前会话和账号均有效，仍可登录；其 role 不产生任何权限。唯一 jti 避免同一账号同一秒登录拿到相同 Token，防止旧 Token 的黑名单影响重新登录。

## 账号身份与任务身份

发布者、参与者是特定任务中的关系。同一账号创建任务 A 后，仍可接取和回答其他账号创建的任务 B；不根据账号 ID 的 user-/worker- 前缀、创建历史或固定角色决定权限。

所有下列 HTTP 请求都需要 Bearer Token：

| 接口 | 身份及业务约束 |
| --- | --- |
| POST /rag-tasks | 发布者取自已认证的 sub；请求体不接收 user_id/worker_id |
| POST /rag-tasks/{id}/accept | 参与者取自 sub；任务存在且 collecting，重复接取和名额已满返回 409 |
| POST /rag-tasks/{id}/answers | 回答者取自 sub；必须有对应参与记录；禁止冒充、覆盖已提交答案或向不再收集的任务新增答案 |
| GET /rag-tasks/{id}、/status、/result | 仅发布者或 accepted/submitted 的参与者可读，无关账号返回 403 |
| POST /rag-tasks/{id}/retry | 仍仅发布者可以操作，保留失败阶段、未知执行确认等原有约束 |

CrowdService/CrowdTaskFacade.snapshot 新增可选 account_id。HTTP 三个查询入口必须传真实认证账号；内部发布、提交和后台处理保留可信 snapshot(task_id) 调用，不模拟 HTTP 身份。

详情访问收口后，未参与账号不能用详情接口读取处理错误或聚合结果。此轮没有新增众包任务大厅；接取依然使用已知任务 ID。若以后需要公开浏览，可单独设计不含处理结果的公开任务摘要。

rag_task_participants 仍按 task_id/worker_id 唯一关联账号。达到人数时的计数、Job 登记、短事务、聚合和入库恢复机制不变。相同已提交正文的幂等重放仍返回原结果，不视为新提交，不重复计数。

遗留 /rag-tasks/legacy/workers 路由仍允许登录账号调用。请求体的 worker_id/responder_id 必须与登录账号一致，实际调用使用当前账号 ID；重复接取返回 409。详情只允许发布者或已接取账号读取，failed/aggregating/completed 任务不能新增人工参与或回答。

## 密码相同是否会产生账号冲突

不会。登录先按用户名查找一个账号，再验证该账号的 password_hash，身份不以密码确定。bcrypt 每次哈希使用随机盐，两个账号设置相同密码也会保存不同哈希；JWT 的 sub 和 Redis 会话键继续区分账号。

知道相同密码的人当然可以配合对应用户名登录任意设置了该密码的账号；随机盐不会改变这个事实。本系统不要求密码在全部账号中唯一，也不保存或比较明文密码来实现这种限制。

本轮使用真实 bcrypt 验证不同账号相同密码、不同盐、正确账号身份、会话隔离及独立登出，没有 Mock 密码哈希或 get_current_user。

密码边界：

- 注册至少 6 个字符；登录保留至少 1 个字符，以兼容已有账号。
- 两个接口统一最多 72 个 UTF-8 字节，拒绝空字符；中文等多字节字符按字节判断。
- 原来可注册但不能登录的 51～72 字节密码，现在可以正常登录。
- 超过 72 字节会返回 422，哈希函数也拒绝截断。不会因前 72 字节相同而把不同长密码当作相同密码。
- 不修改已有 bcrypt 哈希；如果过去保存了超长密码，其原文不能从哈希恢复，需要另外安排密码重置。
- 无法识别的已有哈希返回密码验证失败，登录返回 401，不抛出 500。
- 注册用户名保持 3～50 个字符，拒绝纯空白；不对已有用户名做重命名或强制去空格。

## MySQL 手动迁移边界

本轮只读取 information_schema 的列和索引，没有读取账号行、密码哈希，没有执行 SQL 写入或 DDL。

2026-10-22 只读检查发现：accounts.role 为 VARCHAR(20) NOT NULL、没有默认值；存在普通索引 ix_accounts_role。ORM 当前已经不再 INSERT/SELECT 该列，所以在手动删除之前，新注册仍可能被数据库以“role 没有默认值”拒绝。这不应通过在代码中继续写入假角色来绕过。

已有迁移文件为 migrations/20260817_drop_accounts_role.sql，它显式删除 ix_accounts_role 和 role。请先审核实际部署数据库的 SHOW CREATE TABLE accounts、SHOW INDEX FROM accounts；确认备份和约束后由管理员手动执行。若该列或索引已被删除，不要重复执行原脚本；本轮没有自动迁移或修改此文件。

删除后核对：

1. id、username、password_hash、score 保留，其他表仍引用 accounts.id。
2. 用已有用户名/密码登录；重新注册账号；公开响应不含 role/password_hash。
3. 同一账号发布任务并参与其他人的任务，参与记录与提交者 ID 正确。
4. 客户端移除注册/登录 role 参数及根据账号角色隐藏页面或按钮的逻辑；当前仓库未找到前端 JS/TS/Vue/HTML 源码，无法验证独立前端仓库。
5. 生产 Redis、JWT 配置有效；内存回退仅限开发环境，多进程不能共享内存会话。

## 验证与尚存边界

新增 tests/account/test_account_identity_api.py：隔离 SQLite 的 accounts 表实际没有 role 列；注册、登录、更新评分、发布/接取/提交及详情访问经过真实 HTTP、密码与 JWT/Redis 鉴权。补充 tests/account/test_account_permissions.py 的遗留路由资源权限、重复接取和关闭状态验证。

建议复验命令：

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q tests/account tests/rag_task tests/hcp_mad
.\.venv\Scripts\python.exe -B -m ruff check app/modules/account tests/account
.\.venv\Scripts\python.exe -B -m mypy app/modules/account app/modules/rag_task app/api
```

定向执行账号与众包 API、Facade、模块边界和路由组合测试：156 项通过。其中 tests/account 共 71 项，较任务开始前新增 68 个测试用例。Ruff 通过，相关 mypy 检查 52 个源文件通过，app/tests 共 292 个 Python 文件编译语法检查通过。

最终完整 pytest：1099 项通过、3 项失败（180.43 秒）。失败仍为任务开始前已存在的 RAG SQL 契约差异，分别是 test_crowd_schema.py 的 test_new_sql_matches_all_four_orm_tables_and_existing_answer_alter、test_published_stage_one_migration_is_unchanged，以及 test_execution_architecture.py 的 test_incremental_sql_matches_all_new_orm_columns_indexes_constraints_and_defaults。与本轮基线对比，sql/ 与 migrations/ 文件摘要均未改变，未通过放宽测试断言掩盖差异。

完整回归中的 HCP-MAD Worker/Judge/Debate/恢复、RAG 众包业务及账号用例通过；本文不将 SQLite 测试描述为生产 MySQL 并发验证，没有在生产库上注册或登录测试账号。

仍存在的注册并发边界：当前 MySQL username 只有非唯一索引，账号 ID 仍使用既有 count+查重的编号生成方式。顺序重复用户名注册会被拒绝，但数据库尚不能保证并发注册下的用户名唯一性，ID 生成也可能发生竞争。若需要并发强保证，应单独评估唯一索引、重复历史数据和 ID 生成策略，本轮未擅自修改表结构。

登录限流、密码重置和 MFA 不在现有实现及本轮范围内，因此不能宣称账号体系已达到完整生产安全能力。

## 本轮修改文件

| 文件 | 修改目的 |
| --- | --- |
| app/modules/account/schemas.py | 拒绝账号角色及额外参数，统一用户名和密码输入边界 |
| app/modules/account/security.py | 校验 UTF-8 字节限制，禁止 bcrypt 截断，非法哈希安全验证失败 |
| app/modules/account/service.py | 不签发 role，增加唯一 jti，保持密码和 Redis 会话主链 |
| app/modules/rag_task/api/crowd_router.py | 查询时传入认证账号，执行资源权限校验 |
| app/modules/rag_task/api/worker_router.py | 遗留任务详情核对发布者/参与者身份 |
| app/modules/rag_task/application/crowd_service.py | 基于发布者和真实参与记录校验快照访问 |
| app/modules/rag_task/application/facade.py | 传递快照查询身份，保留内部调用兼容 |
| app/modules/rag_task/application/management.py | 遗留任务重复接取与不可接取状态返回 409 |
| app/modules/rag_task/application/answers.py | 遗留任务结束后不能新增人工回答 |
| tests/account/test_account_identity_api.py（新增） | 无 role 数据库、真实 HTTP/密码/JWT、双重业务身份和失败场景 |
| tests/account/test_account_permissions.py | 保留历史 JWT 兼容，补充遗留资源权限与状态回归 |
| README.md | 说明统一账号与众包资源权限 |
| docs/MODULE_PUBLIC_API.md | 更新认证公开契约 |
| docs/ACCOUNT_IDENTITY.md（新增） | 记录实际行为、手动迁移、密码说明、测试与边界 |

## 明确保留的非账号角色

AiType.WORKER/JUDGE、历史 AI 类型的读取兼容、AnswerSource.WORKER/AI、DebateRole、HCP-MAD 内部角色授权以及 LLM messages 的 system/user/assistant/tool role 均保留。这些分别表示 AI 职责、答案来源、编排角色或模型消息协议，不是账号权限。本轮未修改 HCP-MAD、Agent Loop、Provider 或五张 HCP 表。
