# Agent 中间发言执行

> 2026-10-22 路径更新：本文原 Task 持久化用例已归属 `app/modules/hcp_mad/application/task/`，原 ORM / Repository 分别位于 `hcp_mad/domain/` / `hcp_mad/infrastructure/`；公共执行与纯消息选择已抽至 Agent。普通/RAG 任务使用独立 `rag_task` 表，迁移和部署说明见 [执行层与任务隔离记录](AGENT_EXECUTION_SPLIT.md)。原有阶段行为与事务说明仍保留。

新增的 Worker 直接终局能力使用独立 `TaskFacade.execute_debate_turn()`，详见 [Debate 单次执行](DEBATE_TURNS.md)。本文 execute_agent_message 仍只发布中间消息，不获得完成 Task 的权限；底层领取/失败机制增加受控复用点，默认行为保持不变。

`TaskFacade.execute_agent_message()` 是独立的 Task Application 用例。它执行一次 Agent，发布一条共享 `AGENT_MESSAGE` 并完成对应 Execution；不会创建 `Answer` / `FINAL_ANSWER`，不会更新 `Task.final_result`、完成 Task 或调用聚合、评分。未实现 DebateOrchestrator，也未自动接入旧 HCP-MAD 编排。

## 公开契约

```python
from app.modules.task.application.facade import AgentMessageKind, task_facade

message = await task_facade.execute_agent_message(
    task_id, worker_id,
    round_no=0,
    stage="initial",
    message_kind=AgentMessageKind.INITIAL_POSITION,
    system_prompt="根据任务资料形成初始观点，区分已知事实和待核实问题。",
)
```

必填参数：非负整数 `round_no`、稳定业务阶段 `stage`（1～64 位小写字母/数字/下划线/连字符，以字母开头）、`message_kind` 和非空 `system_prompt`。`stage` 是业务幂等维度，不是 Task 工作流阶段或 Execution 的 thinking/tool_executing 等协议阶段。

可选参数：`context_policy`、`request_builder_factory`、`retry_failed=False`。使用 Facade 注入的 Session 工厂；Agent 查询仍经 Agent 公共入口完成。

返回不可变 `AgentMessageResult`：`task_id`、`ai_id`、`context_id`、`execution_id`、`item_id`、`content`、`message_kind`、`round_no`、`stage`、`idempotency_key`、`reused`。`get_execution_result()` 能读取此 Execution，`answer` 为 `None`；共享条目经 `get_context()` 读取。

执行前和发布前均检查：Task 是 HCP-MAD、AI 已分配且启用、参与者与当前工作流阶段匹配。Worker 仅能在 `workers + answering` 发言；固定 Judge 仅能在 `judging + aggregating` 发布 `COMMENT`。Judge 的该普通评论不评估共识、不完成 Task；旧正式 Judge 用例不变。waiting/failed/completed 或阶段不匹配均拒绝，不领取业务键。成功重放也需通过这些校验，任务进入其他阶段后不会重新启动旧发言。

| message_kind | metadata.debate_kind | 含义 |
| --- | --- | --- |
| initial_position | position | 可由 DebateContextBuilder 引用的初始立场 |
| revised_position | position | 可作为当前立场引用的修正观点 |
| rebuttal | message | 回应/反驳材料，不自动替换当前立场 |
| comment | message | 普通评论，不自动替换当前立场 |

metadata 同时记录 `message_kind`、`round_no`、`ai_id`、`execution_id`、`stage`、`idempotency_key`。`attempt_no` 仅是数据库中同任务同 AI 的执行尝试编号，不能推断辩论轮次。是否将一次反驳升级为修正立场由上层显式选择，本用例不解析文本判断共识或事实正确性。

## 共用真实执行链

```text
TaskFacade.execute_agent / execute_agent_message
  → 正式任务原校验 / 中间发言原子领取与参与者校验
  → TaskExecutionContext：准备共享问题、创建真实 AgentExecution
  → ai_answers._run_task_agent（共用）
      → start Execution
      → 原 ToolFacade + ToolExecutor + PersistentAgentObserver
      → request_builder_factory(真实 AgentExecutionScope)，可选
      → AgentFacade.run_agent → 原 Agent Loop → Provider / 工具循环
      → 正式路径：原 add_ai_answer 事务
      → 发言路径：_MessageStore.complete 事务
```

只有最后的提交策略不同。正式执行依旧调用原 `add_ai_answer()`，没有修改其事务、聚合或 Judge 终局规则。Observer 的每轮请求计数、公开 content 审计和工具协议仍使用原事件路径；工具过程是当前 Execution 的私有历史。一次最终发言可以包含多轮模型与工具调用，但只发布一条共享消息。

初始观点使用基础 Context，不需要构造 DebateState。有两个已提交的 Worker 立场后，上层才可建立真实 `DebateState.positions` 并注入 `DebateRequestBuilderFactory`。同一次执行的首次和工具后续请求继续经过原逐请求构建与预算检查，不重放其他 Execution 的私有协议；Task 不导入 HCP-MAD。

## 事务与幂等

1. **领取短事务**：锁 Task → 校验状态和参与者 → 查询 `tasks.metadata_json.agent_message_runs` → 创建唯一的本次 Execution → 登记 `claimed` → commit。新任务上下文与共享问题也在该事务准备。模型调用前登记已经提交。
2. **执行期**：不持有领取事务。原 Observer 使用自己的短事务记录 running、请求计数、私有工具调用和结果。
3. **完成短事务**：锁 Task → 校验本次键所有权 → 锁 Execution 并确认 running → 追加共享 AGENT_MESSAGE → 设置 finalizing/completed → 登记 completed 与 item_id → 单次 commit。发言与 Execution 完成、幂等结果原子提交。
4. **失败短事务**：完成事务异常先由 Session 退出回滚，再独立锁 Task/Execution，将 Execution 与登记项一起标记 failed。Task 本身不因该用例的异常自动失败。已完成登记不会被失败处理覆盖，以保护“提交已成功但响应丢失”的情况。

业务键为规范 JSON `[task_id, round_no, ai_id, stage]` 的 SHA-256；登记中保存这些可读字段。相同键与不同 `message_kind` / `system_prompt` 的调用返回冲突，防止悄悄改变该发言的语义。上下文快照和构建工厂不参与键计算；执行中不替换工厂，成功后相同键返回第一次已提交的内容，不按新上下文重新生成。

- `claimed`：重复/并发调用返回 409，不能再次调用模型。
- `completed`：返回原 `item_id` / `execution_id` / 文本，`reused=True`，不发布第二条消息。
- `failed`：默认 409；调用者显式 `retry_failed=True` 才可重新领取。每个业务键最多三次，每次分配新 Execution，`execution_ids` 保留尝试历史；成功后不可再重试或覆盖。重试不使用失败 Execution 的私有工具历史。
- 协程取消时，等待领取线程完成，确认是否创建了自己的 Execution，再记录失败。模型请求线程无法强制撤销，晚到响应不会继续发布消息。
- 进程崩溃或失败状态写入本身失败时，键可能长期停留 `claimed`。没有租约过期、自动接管或崩溃恢复；明确拒绝再次执行，避免未知执行仍在进行时重复调用。后续恢复机制必须单独设计。

锁顺序尽量维持 Task → Execution → Context；领取已有上下文时先锁旧 Execution 分配尝试号，再准备共享 Context，避免与 Observer 的 Execution → Context 事件写入形成反向等待。锁不跨网络调用。数据库死锁/锁超时会回滚领取，未成功领取不会发送模型请求。

## 数据库并发保证与边界

- **MySQL 实现路径**：新 Session 中检查 `tasks`、`task_contexts`、`agent_executions`、`task_context_items` 均为 InnoDB，再对已存在的父 Task 行执行 `SELECT ... FOR UPDATE`。所有新用例的登记、重试、完成和失败都经同一父行锁，读取最新 metadata 后整体赋回，因而相同 Task 的并发领取被串行化。表缺失、引擎不是 InnoDB 或无法读取引擎信息时，失败关闭，不发送模型请求。
- 该保证依赖事务 Session、InnoDB、所有相关写入遵循父行锁以及不在外部篡改登记。现有 workflow/正式终局对 Task metadata 的写入也在 Task 锁内，并保留其他 namespace。没有新增数据库唯一索引；任意绕过用例直接写 metadata 的代码不受业务键约束保护。
- **SQLite 实现路径**：显式 `BEGIN IMMEDIATE` 串行化写事务，弥补 SQLite 忽略 FOR UPDATE 的差异。隔离测试验证其自身的并发结果，不能替代真实 MySQL 行锁验证。
- **验证范围**：本轮未连接真实 MySQL 或生产数据库。MySQL 仅验证引擎保护分支及 FOR UPDATE SQL 生成，不能据此宣称已通过 MySQL 并发实测。部署前仍需专用 InnoDB 测试库验证多连接/多进程争抢、死锁/超时及故障恢复。
- 若未来有绕过统一父行锁的其他写入者，应将业务幂等键持久化为独立唯一约束并以条件状态更新领取；本轮没有擅自增加表、列或索引。
- 原子提交保证数据库结果一致，不意味着模型/外部工具副作用“恰好一次”。显式失败重试可能再次执行工具；没有新增工具层幂等或补偿。

## 未实现事项

未实现自动轮次调度、双方同步发布屏障、Judge 共识判断、终局立场提升为正式 Answer、并发观点快照策略、崩溃接管和自动重试。旧独立 Worker/Judge 仍提交正式 Answer。本入口不会将既有 Answer 改为消息，也不会将中间消息计入正式答案数量；普通聚合/评分只读取 Answer，旧 Judge 的正式答案引用筛选保持原样。

## 验证方式

`tests/task/test_agent_messages.py` 保留真实 Facade、Agent Loop、Observer、Provider 请求构建、ToolExecutor 和 Repository，只在 Provider 网络发送处替换模型。使用启用外键的临时 SQLite，拒绝真实业务数据库和模型网络连接。

覆盖初始/修正/反驳/评论元数据、共享消息与完成状态提交前的外部不可见性、提交失败回滚、模型与构建器失败、显式重试次数、并发领取、不同业务键、工具私有历史、真实 Debate 适配器与 Execution 绑定、取消、提交成功后的响应丢失、正式 Answer 回归及 MySQL 存储引擎保护分支。MySQL 分支使用单元替身；其他主要用例使用隔离 SQLite 集成测试。

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests/task/test_agent_messages.py -q -p no:cacheprovider --basetemp tmp/pytest-agent-messages-only
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp tmp/pytest-agent-messages-full-20261019
.\.venv\Scripts\python.exe -B -m ruff check app tests scripts evaluation
.\.venv\Scripts\python.exe -B -m mypy app/modules/task/application/agent_messages.py app/modules/task/infrastructure/agent_message_repository.py app/modules/task/application/ai_answers.py app/modules/task/application/execution_context.py app/modules/task/application/facade.py
```

### 本轮实际结果

| 检查 | 结果 |
| --- | --- |
| 新增测试 | 24 个测试函数，参数化展开为 40 项；其中 3 项为 MySQL 保护/SQL 生成单元测试，37 项使用隔离 SQLite |
| 定向测试 | 95 项通过（中间发言、原 Execution、Task 公共接口、Debate 请求接入、模块边界） |
| 完整 pytest | **574 项通过，34.07 秒**，包含普通 Task、RAG、Tool Calling 和旧 HCP-MAD 回归 |
| Python 语法 | app/tests/scripts/evaluation 共 256 个文件内存编译通过，不生成 pyc |
| Ruff | app/tests/scripts/evaluation 全部通过 |
| 相关 mypy | HCP-MAD、相关 Task/Agent 执行代码共 36 个源文件通过 |
| 全量 mypy | 222 个源文件检查，仍有 4 个未修改文件的 6 个既有错误 |
| 真实 MySQL 并发 | 未执行；没有连接生产或真实 MySQL 数据库 |

完整测试实际使用 `--basetemp tmp/pytest-agent-messages-full-20261019`；定向测试使用 `tests/task/test_agent_messages.py tests/task/test_agent_execution.py tests/task/test_task_public_facade.py tests/hcp_mad/test_debate_request_integration.py tests/test_module_public_api.py`。受限环境测试启动停滞后，终止该次进程，在正常本机权限下完成相同隔离测试。

全量 mypy 的既有问题：`app/modules/rag/domain/vector_store.py:18` 缺少返回实现；`scripts/build_rag_hard_eval.py:457` 缺少类型注解；`evaluation/services/answer_variant_executor.py:227,252,260` 的 usage 类型不匹配；`evaluation/runners/rag_retrieval_runner.py:607` 的检索器变量赋值类型不兼容。相关文件与本轮开始时内容一致。

### 本轮文件清单

| 文件 | 变更目的 |
| --- | --- |
| `app/modules/task/application/agent_messages.py`（新增） | 独立业务用例、消息类型/结果、幂等领取、显式重试、原子发布和失败处理 |
| `app/modules/task/infrastructure/agent_message_repository.py`（新增） | 统一父任务锁、MySQL InnoDB 检查、SQLite 立即写事务 |
| `app/modules/task/application/ai_answers.py` | 提取原执行循环装配和失败处理；正式提交仍调用原 add_ai_answer |
| `app/modules/task/application/execution_context.py` | 共享 Context 准备逻辑可参与调用方的领取事务，普通 prepare 保持原行为 |
| `app/modules/task/application/facade.py` | 公开 execute_agent_message、AgentMessageKind 和 AgentMessageResult |
| `tests/task/test_agent_messages.py`（新增） | 上述 40 项测试 |
| `tests/task/test_task_public_facade.py` | 更新明确的公共方法和类型导出清单 |
| `docs/AGENT_MESSAGES.md`（新增） | 本契约、事务/幂等边界及验证记录 |
| `docs/MODULE_PUBLIC_API.md` | 登记新公开用例和契约 |
| `docs/DEBATE_CONTEXT.md` | 说明真实共享立场来源和适配器接入方式 |

没有修改 Answer 提交实现、Agent Loop、Provider、ToolExecutor、HCP-MAD 实现代码、数据库模型或 SQL，也没有删除文件。
