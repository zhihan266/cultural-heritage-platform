# Debate 单次发言、Worker 直接终局与串行推进

> 2026-10-22 路径更新：本文原 Task 持久化用例已归属 `app/modules/hcp_mad/application/task/`，原 ORM / Repository 分别位于 `hcp_mad/domain/` / `hcp_mad/infrastructure/`；公共执行与纯消息选择已抽至 Agent。普通/RAG 任务使用独立 `rag_task` 表，迁移和部署说明见 [执行层与任务隔离记录](AGENT_EXECUTION_SPLIT.md)。原有阶段行为与事务说明仍保留。

阶段 1 方案 B 保持不变：Worker 的有效 finish_with_answer 直接创建正式终局，不再调用 Judge。阶段 2～4 提供首次评估、严格上下文和串行推进；阶段 5 现已实现上限终局 Judge，见下一节。未知 claimed 自动接管仍未实现；后文保留阶段交付记录。

## 阶段 5：上限终局 Judge（已实现）

只有最后允许 turn 返回 continue 后，DebateOrchestrator 才消费阶段 4 的 `finalization.key`（公开绑定字段称 final_key）。它不是新 key，也不启动第二场 Debate。Task 内部 get_debate_schedule 的 needs_finalization 是可消费状态；编排器进入 FinalizationExecutor，正常最终返回 completed，失败返回 needs_retry/paused。

`TaskFacade.prepare_agent_finalization()` 核对已有预约，`execute_agent_finalization()` 在 Task 锁下领取真实固定 Judge Execution。原 Agent Loop、Observer、Provider/工具执行不变；每次请求用专用 build_finalization 构建全部有效发言/证据，不伪造 active_turn、不重放其他 Execution 私有历史。

输出仅允许原 FinishWithAnswer 模型的 `{decision:"finish_with_answer", message, final_answer}`。message 是公开摘要，不能输出隐藏推理；正式结果只用已校验 final_answer 正文。原 P 保存私有白名单结果与摘要；T 原子创建 Judge AI Answer、共享 FINAL_ANSWER，完成 Execution/Task/finalization 回执。两初始 Worker Answer 保留，共三份正式 Answer；中间发言不进入 answers。judge_execution_id 指向真实终局 Judge，不是最后 Worker 或首次评估 Judge。

非法输出/模型失败同 final_key 最多三次总尝试且需显式 retry_failed；prepared 重放只恢复数据库提交，成功回执重放不重复模型/答案。取消或未知执行保留预约暂停；取消传播，不凭超时接管。最终来源或共享证据变化则失败关闭，不自动换候选。旧 Judge 完成校验不放宽，没有普通 finish_task/聚合/评分。

人工审核现在先锁 Task；处于 finalizing 时拒绝覆盖和发奖励，正式完成后的人工审核保留原行为。其后终局重放若发现结果与原回执不同，报告冲突，不覆盖人工结果或重复审核。完整实现、文件及测试证据见 [阶段 5 报告](DEBATE_ORCHESTRATION_DESIGN.md#16-阶段-5-实际实现终局-judge-原子提交与恢复)。SQLite 测试不代表真实 MySQL 并发已验证，Schema 通过也不等于裁决正确。

## 阶段 4 当前生产行为（2026-10-20）

首次 Judge 的 start_debate 事务提交后，HCP handler 调用公开 `DebateOrchestrator.run_or_resume(task_id)`；已进入 debating/finalizing 的显式恢复直接走编排，不重跑初始 Worker 或首次 Judge。no_debate 和未启用开关的旧路径保持原提交语义。

1. TaskFacade.get_debate_schedule 核对真实 start_receipt、初始 Answer 和 REVIEW，幂等补齐阶段 2 历史数据的调度投影。TaskFacade.reserve_debate_turn 在 Task 锁内比较 debate_id、next_turn、revision、固定顺序和上限，保存 ready 预约。真实 execution_id 在原发言领取事务创建后绑定，绝不伪造。
2. 奇数 turn=A、偶数=B，round=(turn+1)//2；turn 1/2 属于第一轮，初始正式答案属于 round 0。严格 factory 显式传入冻结 initial_positions、最新 positions、全部前序 turn 引用和真实 binding，每次工具后的模型请求仍重建和预算复核；B 必须收到 A 的同轮发言。
3. 有效结果先保存原 private REVIEW 准备记录 P。原发布事务 T 同时写共享发言、Execution/key completed、真实 receipt、positions、completed_turns、next_turn_no、revision 并清空 active_turn。失败整体回滚；编排器不单独补写轮次。
4. continue 未达上限才允许下一 Worker；finish 复用方案 B，在同事务新增终止 Worker 的正式 Answer/FINAL_ANSWER 并完成 Task，保留两份初始 Answer。不调用额外 Judge、普通聚合/评分。
5. 服务端默认 max_rounds=3/max_turns=6，配置 HCP_MAD_DEBATE_MAX_ROUNDS 的范围为 1～16，创建时固化。最后 turn 仍 continue 时，原事务设置 finalizing/limit_reached 和 finalization 预约，Task 暂保持 answering；阶段 5 在该事务提交后消费预约调用固定 Judge。

失败/格式错误不发布、不推进；仅 retry_failed=True 可对已登记且真实 failed 的执行重试，最多三次总尝试。重试保留 turn/key/actor/round/base_shared_digest，通过父锁更换 owner、递增 generation/revision，再创建新的 Execution；旧私有工具历史不转移。prepared 重放只恢复 T，completed 重放只读原 binding/receipt，不重复模型或消息。

取消通过 typed 回调传播 CancelledError，并把活动预约保留为 paused；数据库 Execution failed 不代表底层模型线程或工具已经停止。ready/claimed/running 未知状态和取消状态即使 retry_failed=True 也不接管。没有基于超时的自动恢复，也没有可信停止证明接口；这种边界不能伪装成崩溃自动恢复。共享输入锚点变化同样失败关闭，不偷偷换证据继续。

新增 **41** 项真实编排测试通过；Task/HCP 定向 **551** 项、完整 pytest **823** 项通过，语法 **275** 文件、Ruff、相关 mypy **53** 源文件通过。全量 mypy 保留 4 个未修改文件的 6 个既有问题。SQLite 多连接测试不等于真实 MySQL/InnoDB 验证；本轮未连接真实 MySQL。完整修改清单、事务说明和实际命令见 [阶段 4 实施记录](DEBATE_ORCHESTRATION_DESIGN.md#15-阶段-4-实际实现串行编排原子推进与恢复)。

下文保留单次执行契约与阶段 1 的历史交付记录，遇到自动推进/恢复范围以本节为准。

## 公开入口与输出协议

```python
from app.modules.hcp_mad.application.facade import DebateOutputContract
from app.modules.task.application.facade import DebateTurnBinding, task_facade

# binding 必须取自 TaskFacade.reserve_debate_turn 的真实预约；不是用户/模型自报授权。
result = await task_facade.execute_debate_turn(
    task_id, worker_id,
    binding=binding,
    result_contract=DebateOutputContract(),
    request_builder_factory=debate_request_factory,
    system_prompt=worker_system_prompt,
    retry_failed=False,
)
```

`DebateTurnBinding` 是冻结数据类型：task_id、debate_id、ai_id、round_no、turn_no、revision、owner_token、generation。`stage` 固定派生为 `debate_{debate_id}_t{turn_no}`，不接受 caller 任意指定。round/turn 为正整数，不用 attempt_no 代替。

HCP `DebateOutputContract` 使用 Pydantic v2 严格鉴别联合 ContinueDebate / FinishWithAnswer，禁止额外字段；`json_schema()`、提示词输出要求及测试使用同一模型。公开 content 必须是一个完整 JSON 对象：

```json
{"decision":"continue_debate","message":"本轮完整公开立场","final_answer":null}
```

```json
{"decision":"finish_with_answer","message":"本轮完整公开立场","final_answer":"正式提交给用户的答案"}
```

message 去首尾空白后非空，最多 12000 字符；finish 的 final_answer 同样非空，最多 20000 字符。总原始 content 上限为 262144 UTF-8 字节。重复 key、非对象根、代码围栏、尾随文本、非法 JSON、非有限常量、错误组合、类型强转及无法编码的文本均拒绝。不从“已达成共识”等自然语言猜测控制信号。

Task 只依赖自己的 `AgentResultContract` / `ValidatedAgentResult`，不反向导入 HCP Pydantic 实现。安全错误为 AgentResultValidationError，仅包含固定 code 和白名单 path，不输出原始响应、隐藏字段或 ValidationError.input_value；Pydantic 错误被映射并抑制原始异常链。

## 服务端持久化前提

单次执行入口不会把普通任务改成 Debate。服务端默认关闭；启用后首次 Judge 的 START_DEBATE 事务负责启动，阶段 4 Task 受控用例负责预约。以下是执行前提，**不是生产库初始化脚本**：

| 范围 | 必须满足 |
| --- | --- |
| Task | mode=hcp_mad，执行时 status=answering；固定两个不同 Worker 与独立 Judge，actor 启用且已分配 |
| hcp_mad | stage=debating，保留固定 worker_ids/judge_id |
| hcp_mad.debate | enabled 必须是布尔 true；phase=debating；debate_id、整数 revision 与 binding 一致 |
| active_turn | ai_id、round_no、turn_no、owner_token、generation、派生 stage 匹配；非空 turn_goal 最多 4000 字符 |
| 初次领取 | active_turn.status=ready，无 execution_id；真实 ID 由领取事务创建并绑定 |
| initial_answers | 两条 `{ai_id, answer_id, execution_id, item_id}`，实际指向当前 Task 的两份正式 AI Answer、共享 FINAL_ANSWER 和已完成 Execution，内容和元数据一致 |
| 重试/提交 | 同一业务键与 execution_id 仍拥有当前 active_turn，绑定/revision 未过期；没有其他正式结果 |

服务端判断不依赖模型返回的 ID、角色、轮次或权限。Schema 根本不允许这些字段。普通/未开启 HCP、旧 judging/workers 阶段不能调用新路径绕过旧 Judge 规则。

以上为阶段 1 基础契约。当前生产串行模式还核对 A/B 顺序、上限、共享输入锚点和轮次投影，使用阶段 3 严格 Builder；没有 orchestration_version 的旧单次测试/调用保留其原语义，不会被隐式接管或推进。

## 真实执行链与逐请求预算

```text
TaskFacade.execute_debate_turn
 → _MessageStore.claim（共享原幂等/三次尝试机制，Debate 专用校验）
 → TaskExecutionContext + 真实 AgentExecutionScope
 → ai_answers._run_task_agent
 → 原 PersistentAgentObserver / AgentFacade / Agent Loop
 → _ControlRequestBuilder 包装调用者注入的逐请求 builder
 → 原 Provider 最终 payload / 原 ToolExecutor
 → DebateOutputContract 校验最终公开 content
 → 私有准备事务
 → 独立 Debate 原子提交事务
```

request_builder_factory 为必填依赖，应使用具有最终请求预算复核的实现，通常是现有 DebateRequestBuilderFactory。每个真实 Execution 有独立包装器，每次请求先验证所有权，再调用原 builder，加入同源 Schema、服务端 turn_goal 和安全格式反馈；原 Loop 对完整工具组进行检查，随后将**最终消息、实际 tools、有效 model 和 max_tokens 等 options**一起交给内层 validate_request。

因此 Schema/目标/反馈不会在首次请求后丢失，也不通过假定 other_inputs 自动传给 Provider 来计数。基线 build 的预算报告还不包含包装器新增内容；应以 last_validation 的最终复核为准。新增必需内容超限直接拒绝发送，不再偷偷截断证据或工具结果。自定义 builder 是受信依赖，不能注入空的 validate_request 后宣称获得了预算保证。

默认 Token 计数仍为估算，不是 Provider usage。原 Agent Loop 的实际 usage 统计和事件保持不变；Task 原 Observer 仍主要持久化请求计数/工具轨迹，没有新增持久化 usage 表或把估算写成真实消耗。

只有原 Loop 返回最终 content 后才执行控制校验；工具前普通说明无需伪装成控制 JSON。工具调用和结果保持当前 Execution 的私有历史；其他 Execution 的私有工具记录不进入请求。只读取 Provider 公开 content，不保存 reasoning_content。

## 两个结果分支与事务

**私有准备事务 P**：有效结果通过本地校验后，以 private REVIEW 保存白名单 decision/message/final_answer，标记 include_in_prompt=false，登记 prepared_item_id 和摘要，业务 key 改为 prepared。Execution 保持 running。原始未校验 JSON 不共享、不落为候选。

**发布/终局事务 T**：Task→Execution→Context 锁序下再次核对绑定、真实执行和准备记录，完成以下操作后一次 commit：

| continue_debate | finish_with_answer |
| --- | --- |
| 共享 AGENT_MESSAGE，content=message | 共享 AGENT_MESSAGE，content=message |
| 当前 Execution completed | 当前 Worker 正式 Answer，content=final_answer |
| key completed；旧单次模式 active_turn completed，串行模式释放预约 | 共享 FINAL_ANSWER，关联 answer_id |
| 不创建 Answer/FINAL_ANSWER，不完成 Task | 当前 Execution completed；Task.final_result/completed |
| 阶段 4 生产模式在同事务写 receipt、更新 positions 并推进下一 turn | HCP 完成字段、Debate completed/finalization、业务 key completed；生产模式同事务写最终 turn receipt |

finish 的共享消息、Answer、FINAL_ANSWER、Execution、Task 和终局登记全部在 T 内。P 是私有恢复材料，T 回滚后可能保留；不能把这个私有准备记录当作已经对外完成。

Answer 创建使用 `answers._create_answer_record()` 和原 Repository。source=AnswerSource.AI；score_snapshot 读取同一事务中的实际 AiAgent.score，weight 保持原规则 0.0，ID 规则不变。终止 Worker 是实际 responder，Answer.execution_id 就是本次 Worker Execution。两份初始 Answer 不变，终止后共有三份正式 Answer；不改写或删除初始记录。

Worker 分支不调用旧 `_complete_hcp_task()`，也不放宽它。新分支写入：hcp_mad.stage/status/completed_at/final_answer_id、final_execution_id、final_responder_id、completion_mode=debate_worker；debate.phase=completed、termination_reason=worker_requested_finish、finalization 的 answer/execution/responder/message/final_item/key 关联。**不把 Worker 的 Execution 写成 judge_execution_id**，配置的 judge_id 保持原样。

单方 finish 只表示该 Worker 主动结束，不表示系统已验证双方共识或事实正确。没有额外 Judge、普通聚合、模型评分或人工奖励调用。

## 幂等与失败恢复

沿用 `agent_message_runs` 和 hash([task_id, round_no, ai_id, stage])。当前 fingerprint 固定业务身份 task/debate/AI/round/turn、提示词和同源 Schema；owner/generation/revision 由 Task 的预约及登记绑定单独校验，使受控失败重试能保持同键。claimed 自动接管仍不提供。

- **模型/格式错误**：Execution 与登记 failed；不共享、不创建 Answer。默认不重试，显式 retry_failed=True 才分配新 Execution，同键最多三次。格式反馈来自安全 code/path，加入下一次实际请求并计预算；不把原始响应回填。失败 Execution 的私有工具历史不会转移到新尝试。
- **P 已提交、T 失败**：T 全部回滚，登记保持 prepared，Execution 保持 running；同入口重放仅读取准备结果并恢复 T，不启动模型、不新建 Execution、不消耗模型重试额度。此恢复不要求 retry_failed=True。
- **T 成功但响应丢失**：从 completed 登记核对消息/Answer/FINAL_ANSWER/Execution/Task/终局关联，返回原 DebateTurnResult，reused=True。不会重复发布。
- **P 本身无法持久化**：有效文本没有可靠恢复副本，保留 claimed/running 并记录安全提交错误；重复调用会 409，避免用新模型调用掩盖丢失的有效结果。没有承诺自动修复这类数据库完全不可用/进程丢失场景，需要后续可信恢复流程。
- **过期 owner/revision/Execution**：拒绝发布；不能用换 stage、turn 或 debate_id 绕过重试，因为它们必须与服务端当前预约一致。

有效准备结果不先标 Execution completed 再补 Answer。普通 execute_agent 的旧失败收尾和正式事务未改变；新用例单独区分“模型失败”与“有效结果持久化失败”。

MySQL 路径继续使用现有 InnoDB 检查和 Task SELECT FOR UPDATE；新终局路径额外检查 answers 表也为 InnoDB，防止非事务答案表破坏回滚。SQLite 使用 BEGIN IMMEDIATE。实现依赖所有相关写入者遵守同一 Task 锁/绑定规则。本轮未连接真实 MySQL，不能用 SQLite 多连接测试宣称已验证 InnoDB 并发。外部模型/工具副作用也不是 exactly-once。

## 验证与范围

新增 `tests/hcp_mad/test_debate_control.py`（严格解析/Schema）和 `tests/task/test_debate_turns.py`（真实 Facade/Loop/Observer/Repository）。后者只在隔离 SQLite 中提供未来服务端预约的 fixture，不是生产初始化功能；真实业务 DB、模型网络和外部工具均被禁止。

测试覆盖两种合法结果、非法输出、安全错误、A/B 任一 Worker 直接终止、真实 AI 评分、初始答案不变、工具与实际 usage、Schema/反馈预算、五类终局写入回滚、prepared 并发恢复、成功响应丢失、重复领取、过期绑定和非 Debate 拒绝。

阶段 1 当时没有修改 Builder 或完整编排。阶段 3 增加严格 turn 模式，阶段 4 已在服务端启用的 start_debate 分支自动进入编排；原 Agent Loop、Provider、ToolExecutor 和旧 Judge 终局实现仍未改写。

### 实际验证结果（2026-10-19）

| 检查 | 结果 |
| --- | --- |
| 新增测试 | **65 项通过，14.42 秒**；25 项纯协议、38 项隔离 SQLite、2 项 MySQL 引擎保护单元替身 |
| 定向测试 | 控制/单次终局、中间发言、Task Facade 与模块依赖约束共 117 项通过；后续新增边界由最终新增/全量检查覆盖 |
| 最终完整 pytest | **639 项通过，58.59 秒**，包含 normal/rag、Tool Calling、普通中间发言和旧 HCP-MAD 回归 |
| Python 语法 | app/tests/scripts/evaluation 共 **261** 个文件内存编译通过，不生成 pyc |
| Ruff | app/tests/scripts/evaluation 全部通过 |
| 相关 mypy | **40** 个源文件通过 |
| 全量 mypy | 225 个源文件检查，4 个未修改文件的 6 个既有错误 |
| 真实 MySQL 并发 | **未实测**；没有连接真实 MySQL 或生产数据库 |

实际主要命令：

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests/hcp_mad/test_debate_control.py tests/task/test_debate_turns.py tests/task/test_agent_messages.py tests/task/test_task_public_facade.py tests/test_module_public_api.py -q -p no:cacheprovider --basetemp tmp/pytest-debate-control-target2-20261019
.\.venv\Scripts\python.exe -B -m pytest tests/hcp_mad/test_debate_control.py tests/task/test_debate_turns.py -q -p no:cacheprovider --basetemp tmp/pytest-debate-control-new-final-20261019
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp tmp/pytest-debate-control-final-full-20261019
.\.venv\Scripts\python.exe -B -m ruff check app tests scripts evaluation
.\.venv\Scripts\python.exe -B -m mypy app/modules/hcp_mad app/modules/task/application/agent_results.py app/modules/task/application/debate_turns.py app/modules/task/application/agent_messages.py app/modules/task/application/ai_answers.py app/modules/task/application/answers.py app/modules/task/application/execution_context.py app/modules/task/application/facade.py app/modules/task/infrastructure/agent_message_repository.py app/modules/agent/execution_observer.py app/modules/agent/tool_calling.py app/modules/agent/llm_provider.py app/modules/agent/application/facade.py
.\.venv\Scripts\python.exe -B -m mypy app scripts evaluation
```

全量 mypy 既有问题：`app/modules/rag/domain/vector_store.py:18` 缺少返回实现；`scripts/build_rag_hard_eval.py:457` 缺少注解；`evaluation/services/answer_variant_executor.py:227,252,260` 的 usage 类型不兼容；`evaluation/runners/rag_retrieval_runner.py:607` 的检索器类型赋值不兼容。本轮未修改这些文件。

### 实际文件清单

新增 6 个、修改 10 个文件；没有删除文件、改表或增加依赖。

| 文件 | 原因 |
| --- | --- |
| `app/modules/hcp_mad/application/debate_control.py`（新增） | 同源 Pydantic 控制 Schema、严格解析、安全错误 |
| `app/modules/task/application/agent_results.py`（新增） | 通用结果协议、校验后数据和白名单错误，不依赖 HCP 实现 |
| `app/modules/task/application/debate_turns.py`（新增） | 持久化绑定、逐请求契约包装、私有准备、Worker 原子终局和提交恢复 |
| `app/modules/task/application/agent_messages.py` | 提取可复用领取/校验/失败扩展点，保留默认消息用例语义 |
| `app/modules/task/application/answers.py` | 提取原底层答案创建规则；新增内部 Debate AI 创建帮助函数，不放宽旧 Judge 校验 |
| `app/modules/task/infrastructure/agent_message_repository.py` | 可选把 answers 纳入 InnoDB 检查，原普通消息仍检查其原四表 |
| `app/modules/task/application/facade.py` | 公开新用例和结果/绑定契约 |
| `app/modules/hcp_mad/application/facade.py` | 公开控制模型与解析器 |
| `tests/hcp_mad/test_debate_control.py`（新增） | 25 项严格协议和 Schema 测试 |
| `tests/task/test_debate_turns.py`（新增） | 40 项真实执行/隔离事务及引擎保护测试 |
| `tests/task/test_task_public_facade.py` | 更新具名公共接口清单 |
| `docs/DEBATE_TURNS.md`（新增） | 本阶段真实 API、事务、恢复边界与验证结果 |
| `docs/DEBATE_ORCHESTRATION_DESIGN.md` | 将产品选择改为方案 B，区分阶段 1 与未来编排 |
| `docs/AGENT_MESSAGES.md` | 明确普通消息与独立终局用例的边界 |
| `docs/DEBATE_CONTEXT.md` | 记录外层逐请求契约包装，不改历史规则 |
| `docs/MODULE_PUBLIC_API.md` | 新公共用例与类型说明 |
