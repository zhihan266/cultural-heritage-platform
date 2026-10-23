# Debate 专用上下文构建模块

> 2026-10-22 路径更新：本文原 Task 持久化用例已归属 `app/modules/hcp_mad/application/task/`，原 ORM / Repository 分别位于 `hcp_mad/domain/` / `hcp_mad/infrastructure/`；公共执行与纯消息选择已抽至 Agent。普通/RAG 任务使用独立 `rag_task` 表，迁移和部署说明见 [执行层与任务隔离记录](AGENT_EXECUTION_SPLIT.md)。原有阶段行为与事务说明仍保留。

阶段 3 已实现**可选的严格 turn 上下文**，阶段 4 接入真实串行调度，阶段 5 新增独立终局 Judge 构建路径。初始答案、最新立场、同轮历史、首次 REVIEW、证据及本 Execution 私有工具组均须验证；旧 round 模式继续兼容。严格 turn 和终局契约分别见下文。

本模块只生成单次模型请求的派生输入，不运行辩论循环、模型、工具或检索，不写数据库。阶段 2 的 start_debate 仅提供已启动状态；本阶段验证未来可信预约提供的 active_turn，不创建预约。normal/rag、默认关闭的旧 HCP Judge、正式 Answer 提交规则和数据库结构均保持不变。

## 原 round 模式公开入口（保持兼容）

业务模块通过 `app.modules.hcp_mad.application.facade` 导入 `DebateContextBuilder` 及其配置和返回类型。

以下保留旧 round 示例。它不自动获得严格 turn 语义；新业务应使用本文“阶段 3”中的显式 turn_binding/turn_history。

```python
from app.modules.hcp_mad.application.facade import (
    DebateContextBuilder, DebateContextPolicy, DebateExecutionRef, DebateIssue,
    DebatePosition, DebateRole, DebateState, TokenBudget,
)
from app.modules.task.application.facade import task_facade

# 以下 ID 必须来自当前 Task 已持久化的共享 ContextItem / Execution。
# worker_a_id、worker_b_id 应取自 Task.metadata["hcp_mad"]["worker_ids"]。
state = DebateState(
    task_id=task_id,
    round_no=1,
    positions=(
        DebatePosition(ai_id=worker_a_id, item_id=worker_a_position_item_id),
        DebatePosition(ai_id=worker_b_id, item_id=worker_b_position_item_id),
    ),
    unresolved_issues=(
        DebateIssue(id="construction-date", description="双方是否混淆始建与重修时间"),
    ),
)
result = DebateContextBuilder(tasks=task_facade).build(
    task_id=task_id,
    role=DebateRole.WORKER,
    ai_id=worker_a_id,
    state=state,
    history=(
        DebateExecutionRef(execution_id=worker_a_initial_execution_id, round_no=0),
        DebateExecutionRef(execution_id=worker_b_initial_execution_id, round_no=0),
    ),
    policy=DebateContextPolicy(recent_rounds=2),
    budget=TokenBudget(
        context_window_tokens=16384,
        max_input_tokens=12000,
        reserved_output_tokens=2048,
        safety_margin_tokens=256,
        model_name=target_model_name,
    ),
    tools=actual_request_tool_schemas,
)
messages = result.messages
```

`build()` 是同步只读方法；在异步宿主中应通过 `asyncio.to_thread` 调用，避免阻塞事件循环。需要重放当前执行的私有工具历史时，额外传 `execution_id=current_execution_id`；不传时完全不读私有历史。

单独调用 `build()` 仍只约束一次输入。正式 Agent 执行现在可通过下文的可选逐请求适配器接入，不能只把初始 messages 交给普通 PersistentAgentObserver 后期待它自动保留 Debate 上下文。

### 接入真实 Task 执行：已有观点之后

`DebateRequestBuilderFactory` 已从 HCP-MAD Facade 公开。它在真实 Execution 创建、开始后接收 `AgentExecutionScope(task_id, ai_id, context_id, execution_id)`，每个执行创建独立适配器。Task 不导入 HCP-MAD，仍保留原有 Observer 的事件记录和正式 Answer 提交事务。

```python
from app.modules.hcp_mad.application.facade import DebateRequestBuilderFactory

request_factory = DebateRequestBuilderFactory(
    builder=DebateContextBuilder(tasks=task_facade),
    role=DebateRole.JUDGE,
    state=state,  # 必须已引用两个完成执行的有效共享 Worker 观点
    history=(),
    budget=TokenBudget(
        context_window_tokens=16384,
        max_input_tokens=12000,
        reserved_output_tokens=2048,
        model_name=target_model_name,
    ),
)
# 此示例仍是当前 HCP-MAD 的最终 Judge 执行：必须已处于 judging，
# 两个 Worker 必须各有一条正式 Answer；成功仍会原子完成 Task。
task, answer = await task_facade.execute_agent(
    task_id, judge_id,
    system_prompt="依据共享观点与证据生成综合回答。",
    context_stage="aggregation",
    finalize_task=True,
    request_builder_factory=request_factory,
)
```

此机制没有自动接入现有 JudgeExecutor，也没有创建 DebateOrchestrator；调用方显式注入才生效。`execute_agent` 成功仍提交正式 Answer。中间发言使用独立的 `execute_agent_message`，参见 [中间发言契约](AGENT_MESSAGES.md)；初始观点尚不存在时使用基础 Context，不使用此适配器。

每次请求的真实调用顺序：

```text
TaskFacade.execute_agent(request_builder_factory=...)
  → invoke_ai_for_task：创建并 start Execution，调用 factory(scope)
  → PersistentAgentObserver：保留 on_event，携带 request_builder
  → AgentFacade.run_agent → 原 Tool Calling Loop
  → Provider.build_llm_payload：确定实际 model / tools / generation_config
  → 可选 prepare_messages 钩子
      → DebateRequestBuilder.build_messages → DebateContextBuilder.build
      → 消息公开字段白名单、工具历史完整性核对
      → DebateRequestBuilder.validate_request → check_budget
      → 通过后才记录 llm_request
  → 原 _request_tool_calling_sync 发送最终 payload
  → 工具调用和结果持久化，下一次请求重复同一过程
  → Loop 最终文本 → 原 Answer 提交事务
```

自定义模式跳过普通 Observer.build_messages，不将两套上下文相加。默认未传工厂时，原 Observer、Provider 调用签名、消息选择及事件顺序保持原有路径。`AgentFacade.run_agent_with_usage` 也复用同一 Loop 钩子，真实 usage 机制不变。

`AgentRequestInput` 是 Provider 已解析请求的独立快照，只含 messages、tools、其余请求 options，不含鉴权头。适配器使用实际工具 schemas、实际模型、tool_choice 和所有实际生成参数计数；它不能通过修改快照改变 Provider 的工具、模型或参数。公开消息白名单处理后再次检查最终预算；第二次只复核，不压缩。每次请求保留原 Loop 已有的完整工具组，缺失、孤立、重复、错配或额外注入的工具组会在网络发送前失败。

输出预留取配置预留与实际 `max_tokens` 的较大值。预算未指定 model_name 时绑定实际模型，已指定但不匹配则报错；实际请求缺少正整数 max_tokens 时明确拒绝，不猜测服务端默认值、不修改 generation_config。计数依然是所选计数器的估算，并非真实 usage。

`last_result` 和 `last_validation` 仅保存于当前执行的适配器实例，供调用方诊断，不新增数据库字段。预算不足、构建器异常或协议校验失败时该次请求不发送；原执行异常处理会将 Execution 置为 failed，不产生该执行的正式 Answer。Task 是否失败继续由既有上层编排决定。

### 参数与返回类型

| 参数 | 类型 / 规则 |
| --- | --- |
| `task_id` | 当前 HCP-MAD Task ID，必须与 `state.task_id` 一致 |
| `role` | `DebateRole.WORKER` 或 `DebateRole.JUDGE`，不接受任意角色字符串 |
| `ai_id` | Task 固定的两个 Worker 或 Judge，必须符合指定角色 |
| `state` | `DebateState`，包含轮次、两个当前观点引用、论据、分歧、新信息与可选摘要 |
| `history` | `Sequence[DebateExecutionRef]`，显式指定 Execution 属于哪一轮 |
| `execution_id` | 可选；只允许当前 Agent 的当前执行，不允许读取另一个执行的私有工具历史 |
| `policy` | `DebateContextPolicy`，只能缩小选择范围，不能提高可见性 |
| `budget` | `TokenBudget`，指定窗口、输入上限、输出预留、安全余量和模型名 |
| `tools` | 实际将发送给模型的工具 schemas，参与预算，不触发工具执行 |
| `other_inputs` | 其他真实请求输入，例如 response_format；不允许覆盖 messages / tools |

返回 `DebateContextResult`：`messages`、实际使用的 `evidence`、`included_history`、`budget` 报告、Task / Context / 版本 / Agent / 角色 / 轮次，以及证据去重数量。工具组的历史记录列出组成它的全部原始条目 ID；共享发言作为 user 引用资料，不伪装成当前 Agent 自己的 assistant 历史。

## Worker 与 Judge 的差异

两种角色都保留原始问题、现有任务格式约束、双方完整当前观点、关键论据、未解决分歧、新信息及必要证据。角色职责与当前轮次目标集中在 `application/context/prompts.py`。

- Worker 默认读取最近两轮、已显式关联的相关共享发言；可以读取自己当前 Execution 的完整工具组及公开消息。
- Judge 默认不附加 Worker 原始历史，只读取双方完整观点、结构化状态、证据和摘要；可以显式开启 `judge_include_recent_history`。不允许重放 Worker 私有工具历史。
- 必要历史摘要可设 `require_history_summary=True`，此时超限也不能删除。
- 本模块不生成 Judge 判定，不解析共识结果，不触发后续分支。

## 状态和轮次归属

本节保留原 round 模式规则。严格 turn 模式使用独立 initial_positions、完整 DebateTurnRef 和共享摘要，不适用下文 round-only 历史窗口；实际契约见末尾阶段 3。

`DebateState` 是调用方在内存中组织的派生状态，不是新的历史数据库。Pydantic 契约使用严格类型、冻结字段并禁止多余字段；双方必须是不同 Worker，已解决与未解决议题的 ID 不得重复。

当前观点引用的条目必须：属于当前 Context、共享可见、非隐藏、内容非空，与 Worker / Execution 归属一致，且执行已完成。支持现有 `FINAL_ANSWER`，以及明确标记 `metadata.debate_kind="position"` 的共享 `AGENT_MESSAGE`（可由 `execute_agent_message` 发布）。如果存在更新的可见观点，旧引用会被拒绝；不使用摘要替代原始完整观点。

历史归属只使用 `DebateExecutionRef(execution_id, round_no)`。不会根据 `attempt_no`、全局 `sequence_no` 或两条消息是否相邻来推断轮次。`sequence_no` 只在轮次归属明确后用于排序。相同 Execution 不得同时属于两轮，未来轮次被拒绝；已持久化的 `metadata.round_no` 若与显式关联冲突，也会被拒绝。

`recent_rounds=2` 在当前轮 r 选择 `[max(0, r-2), r)` 的共享历史。未关联的执行、过旧执行、非参与者执行不会被无差别注入。可用 `relevant_item_ids` 进一步收窄；这里不声称实现了自动语义相关性分类。

## RAG、引用和压缩

只读取当前 Task Context 的最新任务级共享 `RAG_CONTEXT`。`use_rag=False`、最新空记录或检索失败标记都会得到无证据结果，不退回旧版本、不重复检索。跨 Context 的条目、私有资料和执行级伪装 RAG 被排除。

支持现有 RagFacade 的 `[资料N]`、`[公共知识库资料N]`、`[用户上传资料N]` 格式。证据引用使用 `EvidenceReference(item_id, label)`，筛选后不重新编号。相同正文合并为一块时保留全部引用别名和来源；不把不同来源的相同正文说成独立佐证。未知格式作为一个整体，以原 ContextItem ID 追溯，不能据此伪造文件来源。

持久化文本没有检索分数，因此保留原排序，允许用 `preferred_evidence_refs` 显式提高相关证据优先级。结构化论据和议题引用、以及 `required_evidence_refs` 指定的证据必须保留；没有显式关键引用时至少保留首块。状态里的引用必须解析到本次有效 RAG，不能引用旧版本、别的 Task 或私有内容。对方完整答案中的自由文本引用仍需上层标注，构建器不会自动推断其语义依赖。

压缩先完成证据去重、执行范围与历史窗口筛选，再在超预算时依次删除可选块：非必要摘要、已解决议题说明、较旧轮次交互、未优先标记的非关键证据、最近轮次交互、显式优先但非必需的证据。相同优先级下先去掉排序靠后的资料。

原始问题、格式约束、角色与本轮目标、双方完整当前观点、关键论据、未解决分歧、新信息、必要证据、当前执行的已选工具组不参与删除。不会截断整个拼接字符串，也不会截断年份、数字、证据正文、来源或工具 JSON。原始持久化数据不变。

## 工具协议与可见性

通过 `TaskFacade.get_context()` 和 `get_execution_result()` 读取现有快照，再复用 Task `ContextBuilder.select_items()` 的上下文、可见性、归属及隐藏标记规则。

Task ContextBuilder 新增公开的 `build_history_groups()`，返回单条 assistant 消息或完整 assistant tool_calls + 全部 tool 结果组，并保留原始条目引用；原消息构建仍使用同一套规则。不同执行或 Agent 的结果、缺失结果、重复结果、step 不匹配和无效协议不会被拼接。Debate 的轮次/相关性过滤在整组上执行，不在筛选后重新组装残缺协议。

私有工具消息只有在 `task_id / context_id / execution_id / ai_id` 全部匹配，且当前执行处于 waiting / running 时才允许进入输入。其他执行即使同属一个 Agent，其工具历史也不会重放。共享的对方发言作为引用资料，协议 JSON 只转发可见 content；工具消息仅保留已有协议白名单字段。原 round 模式共享与私有快照版本不一致时仍返回 `DebateContextError`。严格 turn 模式改为共享内容摘要与业务绑定核对，允许当前执行私有事件使全局 Context.version 增长，详见阶段 3 契约。

这是服务内上下文选择接口，不替代 HTTP 层用户身份鉴权。上层提供的摘要、论据和新信息应已经被确认可共享；构建器不能识别调用方主动写进自由文本的秘密或验证其事实真实性。

## Token 预算

有效输入上限：

```text
min(max_input_tokens,
    context_window_tokens - reserved_output_tokens - safety_margin_tokens)
```

计数器收到完整 messages（含 system）、完整工具定义和 `other_inputs`。额外输入不能覆盖 messages / tools；显式 model 必须与预算匹配；显式 max_tokens / max_completion_tokens 不得超过预留输出空间。宿主还须让实际 Agent 配置与所传模型、工具及输出预留一致；构建器不修改 generation_config。

- 仓库依赖没有本地 LLM Tokenizer；已有 `llm_usage.py` 记录响应真实 usage，并不是输入计数器。
- 默认 `Utf8TokenEstimator` 按完整序列化输入的 UTF-8 字节数加固定开销作保守代理，明确 `is_estimate=True`；这不是所有模型的严格上界，可能明显高估并较早触发预算不足。
- `TokenizerTokenCounter(model_name=..., encode=...)` 可注入已加载的目标模型词表，不联网、不安装新依赖。词表匹配会检查，但序列化 JSON 与实际聊天模板仍可能不同，因此仍标记为估算。
- 可注入实现 `TokenCounter.count_request()` 的目标模型完整请求计数器。只有匹配真实模型模板的实现才应把 `TokenEstimate.is_estimate` 设为 False；即使如此，本地计数也不是账单。
- 每移除一个完整可选块都会重新计数，最多有限次线性尝试。报告包含前后计数、计数器与模型、输入限额、输出预留、删除块 ID 和计数次数。
- 必需块仍超限时抛 `TokenBudgetExceeded`，包含 `.report` 与 `.required_block_ids`，不返回伪装成功的残缺上下文。
- 实际消耗继续以现有 Provider usage / `run_agent_with_usage` 返回值为准，本次没有更改真实 usage 记录机制。

## 文件与持久化复用

新增：

- `app/modules/hcp_mad/application/context/__init__.py`
- `app/modules/hcp_mad/application/context/debate_context_builder.py`
- `app/modules/hcp_mad/application/context/debate_context_policy.py`
- `app/modules/hcp_mad/application/context/debate_state.py`
- `app/modules/hcp_mad/application/context/evidence.py`
- `app/modules/hcp_mad/application/context/prompts.py`
- `app/modules/hcp_mad/application/context/token_budget.py`
- `app/modules/hcp_mad/application/context/request_adapter.py`：后续接入阶段新增的逐请求适配器与工厂。
- `tests/hcp_mad/test_debate_context.py`
- `tests/hcp_mad/test_debate_request_integration.py`：后续接入阶段的真实执行链隔离测试。
- `docs/DEBATE_CONTEXT.md`

最小修改：

- `app/modules/task/application/context/builder.py`：公开已有工具配对能力及完整原始条目分组；只读 Protocol 支持冻结快照，并在配对时核对执行归属。
- `app/modules/task/application/facade.py`：公开 ContextBuilder / ContextHistoryGroup；ContextItemView 增加只读 metadata_json 兼容属性，避免重新制造一套条目模型。
- `app/modules/hcp_mad/application/facade.py`：重新导出构建器和公开契约，不改变既有执行流程。
- `docs/MODULE_PUBLIC_API.md`：记录新增公开类型入口。
- `tests/task/test_task_public_facade.py`：公开契约断言增加两个只读构建类型，继续禁止导出仓储与状态修改能力。

没有增加表、列、迁移、写入接口或通用上下文仓库。读取链仍是 TaskFacade → TaskExecutionContext → 现有 execution_repository。原始历史继续保存在 TaskContext / ContextItem 中；未来若持久化共享状态，可评估共享 REVIEW / AGENT_MESSAGE 与 metadata，但本次不写入摘要或预设自动恢复机制。

## 验证与限制

独立测试使用只读内存替身和固定计数器，显式禁止真实业务数据库、模型及 RAG 调用；另有隔离 SQLite 集成测试，验证通过真实 TaskFacade / Repository 读取已有条目，构建前后 Context 不变且不提交正式 Answer。

2026-10-19 独立上下文构建阶段的验证结果：

| 检查 | 结果 |
| --- | --- |
| 完整 pytest | 516 项通过，28.54 秒；新增上下文测试 64 项 |
| Python 内存编译 | app / tests / scripts / evaluation 共 251 个 Python 文件通过，不生成 pyc |
| Ruff | app / tests / scripts / evaluation 全部通过 |
| 相关 mypy | HCP-MAD 全模块、Task context 与 Task facade 共 31 个源文件通过 |
| 全量 mypy | 6 个既有错误，分布于 4 个本任务未修改的文件 |

完整 pytest 在受限环境停滞后改在正常本机环境完成，测试仍使用离线替身和隔离 SQLite。最终主要命令：

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp tmp/pytest-debate-context-final-20261019
.\.venv\Scripts\python.exe -B -m ruff check app tests scripts evaluation
.\.venv\Scripts\python.exe -B -m mypy app/modules/hcp_mad app/modules/task/application/context app/modules/task/application/facade.py
.\.venv\Scripts\python.exe -B -m mypy app scripts evaluation
```

全量 mypy 的既有问题：`scripts/build_rag_hard_eval.py:457` 缺少类型注解；`app/modules/rag/domain/vector_store.py:18` 缺少返回实现；`evaluation/services/answer_variant_executor.py:227,252,260` 混用 LlmUsage / LlmRequestUsage；`evaluation/runners/rag_retrieval_runner.py:607` 的检索器变量赋值类型不兼容。本任务未修改这些文件。

### 逐请求接入阶段的验证范围

接入测试使用真实 TaskFacade、AgentFacade、PersistentAgentObserver、Agent Loop、Provider 请求组装、ToolExecutor 和 Answer 提交，只在 `_request_tool_calling_sync` 替换网络发送；数据库为隔离 SQLite，工具为无外部依赖的测试工具。覆盖首次与工具后续请求、有效模型及完整工具 schemas、输出限制、超限阻断、协议完整性、构建器异常、普通任务默认路径及 usage 入口。

接入阶段新增 2 个文件、修改 13 个文件，没有删除文件：

| 文件 | 原因 |
| --- | --- |
| `app/modules/agent/execution_observer.py` | 公开请求快照与可选构建/复核协议；核对当前 Loop 的完整工具历史 |
| `app/modules/agent/tool_calling.py` | 每次请求进入可选准备钩子，构建和预算通过后才记录请求事件 |
| `app/modules/agent/llm_provider.py` | 已解析 payload 发送前调用可选消息钩子；沿用原 HTTP、鉴权及响应解析 |
| `app/modules/agent/application/facade.py` | 公开 AgentRequestBuilder / AgentRequestInput 契约，运行方法签名不变 |
| `app/modules/task/application/execution_context.py` | 真实执行 Scope 和工厂类型，原持久化 Observer 携带可选构建器 |
| `app/modules/task/application/ai_answers.py` | Execution 创建并开始后绑定工厂；继续原失败处理及 Answer 事务 |
| `app/modules/task/application/facade.py` | 可选 request_builder_factory 参数与绑定契约 |
| `app/modules/hcp_mad/application/context/request_adapter.py`（新增） | 绑定真实执行与已有 State，按实际请求逐次构建、推导有效预算并复核 |
| `app/modules/hcp_mad/application/context/debate_context_builder.py` | 增加 check_budget 复核最终消息，不再次删减必要输入 |
| `app/modules/hcp_mad/application/context/__init__.py` | 公开适配器及工厂 |
| `app/modules/hcp_mad/application/facade.py` | 从业务公共入口导出适配器及工厂 |
| `tests/hcp_mad/test_debate_request_integration.py`（新增） | 18 项真实执行链隔离测试 |
| `tests/task/test_task_public_facade.py` | 公共只读导出清单增加 Scope / 工厂类型 |
| `docs/DEBATE_CONTEXT.md` | 接入方式、真实调用链、预算约束与验证结果 |
| `docs/MODULE_PUBLIC_API.md` | 更新可选参数、公共协议与职责说明 |

接入阶段最终结果：完整 pytest **534 项通过，33.63 秒**；新增集成测试 **18 项通过**；Python 内存编译 **253 个文件通过**；全仓库 Ruff 通过；相关 mypy **34 个源文件通过**。全量 mypy 仍是上述 4 个未修改文件的 6 个既有错误（共检查 220 个源文件），没有顺手修复。

实际执行的主要检查命令：

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests/hcp_mad/test_debate_request_integration.py -q -p no:cacheprovider --basetemp tmp/pytest-debate-request-integration-20261019
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp tmp/pytest-debate-request-full-20261019
.\.venv\Scripts\python.exe -B -m ruff check app tests scripts evaluation
.\.venv\Scripts\python.exe -B -m mypy app/modules/hcp_mad app/modules/task/application/execution_context.py app/modules/task/application/ai_answers.py app/modules/task/application/facade.py app/modules/agent/execution_observer.py app/modules/agent/tool_calling.py app/modules/agent/llm_provider.py app/modules/agent/application/facade.py
.\.venv\Scripts\python.exe -B -m mypy app scripts evaluation
```

中间发言提交和业务轮次幂等由独立 `execute_agent_message` 用例提供，详见 [中间发言契约](AGENT_MESSAGES.md)。上述为逐请求接入阶段的历史记录。阶段 1～3 提供控制协议、首次 Judge 和 turn 上下文；阶段 4 现已接通预约/串行推进及重试共享锚点，见 [编排实施记录](DEBATE_ORCHESTRATION_DESIGN.md#15-阶段-4-实际实现串行编排原子推进与恢复)。未知执行仍不自动恢复，不自动重试版本冲突。

## 阶段 3：基于 turn 的严格上下文（2026-10-20 已实现）

这是可选的只读构建模式。读取阶段 2 已提交的 Debate 和阶段 4 Task 受控用例预约的 active_turn，验证后生成一次模型输入；Builder 自身不创建 Execution、不调用 Worker、不推进轮次、不提交 Answer。

### 状态、引用和调用契约

| 契约 | 实际字段与兼容规则 |
| --- | --- |
| DebateState.initial_positions | 可选的两个 DebatePosition；默认 None 保留原 round 模式。严格 turn 模式必须显式提供，且与 Task 的两个冻结正式 Answer/FINAL_ANSWER/completed Execution 一致，不从当前 positions 猜初始答案 |
| DebateState.positions | 双方最新有效立场；严格模式只用本 Debate 已提交的 revised_position 替代初始答案，普通 comment/rebuttal 不替代 |
| DebateTurnRef | debate_id、turn_no、round_no、ai_id、execution_id、item_id、message_kind（revised_position/rebuttal/comment）；严格类型、正数轮次、round_no=(turn_no+1)//2 |
| DebateTurnBinding | 复用 Task 已公开的真实业务绑定，未另建编号；父状态和 active_turn 必须匹配，奇数 turn 固定 Worker A、偶数固定 B |
| DebateInputSnapshot | 冻结 task/context/execution/AI/debate/turn/round、共享内容摘要、请求/状态契约摘要、已完成来源 Execution 摘要；不包含正文、不冻结 Context.version，不是授权或数据库预约凭证 |
| build 新关键字 | turn_binding=None、turn_history=()、shared_snapshot=None；显式 turn_binding 才进入新模式，不允许混用旧 history 的 round 引用 |
| DebateRequestBuilderFactory | 在原字段之后增加可选 turn_binding / turn_history；默认旧调用不变。每个真实 Scope 的适配器独立维护 shared_snapshot |
| 返回值 | 原 DebateContextResult 增加 turn_no/debate_id/shared_snapshot，默认 None；IncludedHistory 增加 turn_no/debate_id，原字段和导入入口仍兼容 |
| DebateContextPolicy.required_turn_nos | 默认空集合，显式指定较旧但必需的前序 turn；不能指定当前/未来 turn |

严格模式提供完整的前序已提交 turn 清单，再由策略/预算裁剪可选旧历史。清单必须与当前 Debate 的共享记录一一对应、turn 从 1 连续到 current_turn-1，且 item/Execution/turn 不重复。这样缺失前一发言、跳过尚未提交的 turn、伪造 ID 都会在选择前被拒绝。初始正式 Answer 始终 round=0，绝不伪造 turn=0 或从 attempt_no/sequence_no 推断归属。

```python
from app.modules.hcp_mad.application.facade import (
    DebateContextBuilder, DebatePosition, DebateRole, DebateState,
    DebateTurnRef, DebateRequestBuilderFactory, TokenBudget,
)
from app.modules.task.application.facade import DebateTurnBinding, task_facade

task = task_facade.get_task(task_id)
debate = task.metadata["hcp_mad"]["debate"]
active = debate["active_turn"]  # 必须来自可信预约；阶段 2 本身不创建它
binding = DebateTurnBinding(
    task_id=task.id, debate_id=debate["debate_id"], ai_id=active["ai_id"],
    round_no=active["round_no"], turn_no=active["turn_no"], revision=debate["revision"],
    owner_token=active["owner_token"], generation=active["generation"],
)
initial_positions = tuple(DebatePosition(ai_id=ref["ai_id"], item_id=ref["item_id"])
                          for ref in debate["initial_answers"])
state = DebateState(
    task_id=task.id, round_no=binding.round_no,
    initial_positions=initial_positions,
    positions=current_positions,  # 来自本 Debate 最新已完成的有效立场引用，不能是模型自报 ID
)
history = tuple(DebateTurnRef.model_validate(ref) for ref in committed_turn_refs)
factory = DebateRequestBuilderFactory(
    builder=DebateContextBuilder(task_facade), role=DebateRole.WORKER, state=state,
    turn_binding=binding, turn_history=history,
    budget=TokenBudget(context_window_tokens=32768, max_input_tokens=24000,
                       reserved_output_tokens=2048),  # 按部署模型真实容量配置
)
# 这里只装配工厂，不自动执行 Worker。已有单次执行用例创建真实 Scope 后才调用 factory。
```

只读单次检查也可显式 `builder.build(..., turn_binding=binding, turn_history=history, execution_id=real_id)`，但必须已有匹配的 claimed Execution。后续直接调用时传回 result.shared_snapshot 才能跨请求检查同一快照；正式逐请求工厂会自动保存/复用，不必手动管理。`builder.check_turn_snapshot(snapshot)` 是发送前只读复核接口，不提供修改或恢复入口。

### 初始答案、当前立场、历史的验证与选择

1. 核对 HCP 已启用、Task answering、stage/phase debating、固定 worker_order、策略版本和 schema_version。绑定、revision、owner/generation、目标、真实 execution_id 与当前 claimed journal 一致；业务键使用原 hash([task_id,round_no,ai_id,stage])。
2. 初始答案必须恰好两份正式 AI Answer；每份与 Task.answers、ExecutionResult.answer、唯一共享 FINAL_ANSWER、answer_id、正文、Task/Context/AI 归属及 completed 状态一致。它们不参加 superseded 判断，所以后续新立场不会使初始引用失效。
3. 当前 positions 必须是本 Debate 按显式 turn_no 的最新 revised_position，或尚无修订时的初始答案。初始与当前指向同一 item 时仅渲染一次正文，并在 roles 保留 initial_position/current_position 两种语义。
4. 每条历史同时核对显式引用、共享 ContextItem metadata、真实 completed Execution 和 agent_message_runs completed 发布回执。当前/未来 turn、其他 Debate、非参与者、未提交发言、错配轮次/角色、同一执行重复引用全部拒绝。其他 Debate 的未引用消息不会成为当前立场或历史。
5. 历史以 turn_no 排序，顺序不取决于 sequence_no/attempt_no。当前轮的已提交前序 turn **始终必需**，不受 recent_rounds=0 或 relevant_item_ids 排除；较旧发言再按 recent_rounds、相关条目和 required_turn_nos 选择。

| 当前发言 | 实际必需内容 |
| --- | --- |
| A：round 1 / turn 1 | 两初始答案、双方当前立场、已完成首次 Judge REVIEW/分歧、当前有效 RAG、原始问题/格式、服务端目标、同源 Worker 输出协议；历史清单为空 |
| B：round 1 / turn 2 | 上述内容，外加 A 的 turn 1 共享发言及真实 Execution/item/轮次，不能因为同轮被过滤或压缩 |
| A：round 2 / turn 3 | 初始答案仍保留；双方最新立场与 turn 1/2 的前序历史按显式归属进入，较旧历史才可受策略/预算选择 |
| 当前执行工具后续请求 | 同一共享业务快照，动态读取本 Execution 完整私有工具组，重新校验工具协议与实际请求预算 |

当前生产 execute_debate_turn 发布 revised_position。新引用能验证已有合法 comment/rebuttal 记录并保持其历史语义，但本阶段不新增在 debating 发布这些类型的业务用例，也不放宽原 Task 阶段权限。

### 首次 Judge REVIEW 和证据

REVIEW 必须匹配阶段 2 的 review_key、policy_version、冻结初始引用、completed review journal、start_receipt、真实 Judge Execution 和唯一 Debate ID；该 Execution 必须 completed 且没有正式 Answer。review_key 还会根据 Task/策略/初始 Answer IDs 重算。

正文只接受 reason/substantive_disagreements，再通过同源 JudgeTriggerContract 校验。分歧引用必须同时属于评估 journal 登记的实际输入白名单和**本 turn 可见的初始答案/最新有效任务 RAG**。不能只相信 REVIEW JSON 或其自报白名单；伪造、跨 Context/Task、私有资料、失效/旧 RAG 标签都不能作为引用。实际使用的 RAG 分歧引用会成为必需证据，不被预算删掉。

RAG 继续复用已有证据提取和去重：只选 use_rag 允许的最新任务级 shared RAG_CONTEXT，执行级私有资料不参与；retrieval_failed 或空失效项阻止回退旧版。正文去重保留所有来源和引用别名，兼容普通/公共知识库/用户上传格式。若 REVIEW 引用了已失效证据，明确报错，不偷偷换到另一个版本。

### 同一 Execution 的共享快照和私有工具增长

共享摘要基于 Task 业务数据、已选作用域的共享条目及完成来源记录，不包含全局 Context.version 或当前 Execution 的请求计数/阶段。每次构建前后验证共享来源，后续请求比较同一个 snapshot；最终实际 payload 预算通过后再次检查。共享 RAG、初始答案、立场、历史、目标、绑定或来源执行状态有变化时抛 DebateContextError，不自动切换输入。

当前 Execution 的私有 TOOL_CALL/TOOL_RESULT 每次重新读取；Context.version 增长本身合法。复用 Task ContextBuilder.build_history_groups 配对，完整 assistant tool_calls 与全部 tool 结果作为必需块；孤立、缺失、重复或归属/step 冲突的组拒绝。不能用 policy 关闭或部分过滤必需工具组。对方 Execution、同一 AI 的旧 Execution 私有记录都不重放；共享发言始终为 user 引用资料，不冒充本 Agent 的 assistant 历史。

这是一致性复核的乐观读方案，不跨模型调用持有数据库锁，也没有新增数据库原子快照/租约。输入摘要在每个适配器实例内保存，**不是 turn 预约或跨进程恢复凭证**。阶段 4 另在 Task 锁内预约/推进，并保存排除协调字段和私有事件的 base_shared_digest，防止失败重试更换基础共享输入；本适配器仍逐请求检查更完整的执行内快照。

### 必需块与预算

| 必需，不可静默删除 | 可选，按完整块删除并重新计数 |
| --- | --- |
| system、task、positions（初始/当前双重角色）、review、turn_control（真实目标+Worker Schema）、state 中关键论据/未解决问题/新信息 | resolved、非必需 summary |
| 当前轮前序 turn、required_turn_nos 指定的旧 turn | 其余较旧历史 |
| 显式/论据/议题/REVIEW 引用的必需 RAG，未显式指定时至少第一块 | 未标为必需的非关键 RAG |
| 当前 Execution 完整 own 工具组 | 不允许拆组或截断工具 JSON/正文 |

Worker Schema 来自现有 DebateOutputContract.json_schema()，没有手写第二份协议。原 Task 控制包装仍追加同源 Schema、目标和安全反馈，旧行为保留；最终 validate_request 对实际 payload 的所有内容统一计数，必须以 last_validation 为最终预算依据。额外协议块可能使最终预算超限，此时拒绝发送，不回退为无约束回答。

保留实际模型名、完整工具 Schema、生成参数和输出上限复核；输出预留仍取配置和实际 max_tokens 较大者。默认 UTF-8 字节代理和注入词表计数是估算，不等于真实模型 token/usage。必需内容仍超预算时 TokenBudgetExceeded 包含 report 和 required_block_ids；不会截断整段消息、关键证据或 JSON 来伪装通过。

本构建器不执行轮次或写入状态。阶段 4 已接通 Worker 严格 turn，阶段 5 已提供独立终局 Judge 上下文，状态修改均由 Task 用例负责。共识正确性评估和自动 AI 摘要未实现；预算不能替代 usage，也不能保证裁决正确或资料可靠。

### 阶段 3 实际文件与验证

本轮新增 models.py / turn_context.py / test_turn_context.py，修改 11 个相关文件，无删除或迁移；完整清单和原因见[阶段 3 实施报告](DEBATE_ORCHESTRATION_DESIGN.md)。Task/Agent/Tool、旧 Judge 和正式提交事务均未修改。

新增 **68 项通过**（4 项纯契约、64 项隔离 SQLite），最终完整 pytest **782 项通过，152.92 秒**；定向 Task/HCP-MAD 回归 521 项通过。272 个 Python 文件语法通过，Ruff 全部通过，相关 mypy 51 个文件通过。额外全量 mypy 仍为 4 个未修改文件中的 6 个既有错误（234 个文件检查），未顺手修复。

主要最终命令：

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests/hcp_mad/test_turn_context.py -q -p no:cacheprovider --basetemp tmp/pytest-turn-context-new-verified-20261020
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp tmp/pytest-turn-context-verified-full-20261020
.\.venv\Scripts\python.exe -B -m ruff check app tests scripts evaluation
```

完整定向、相关/全量 mypy 和内存语法命令见实施报告第 14.6 节。新测试复用真实阶段 2 fixture、Facade、Loop、Observer 和持久化，仅替换模型发送；工具为内存替身，数据库为临时 SQLite，生产库和真实模型访问被禁止。SQLite 不证明真实 MySQL 并发正确，本轮未连接或验证 MySQL。

## 阶段 5：终局 Judge 的独立上下文（已实现）

入口为 `DebateContextBuilder.build_finalization(scope, binding, budget, tools, other_inputs, snapshot=None)`（全部为关键字参数）。scope 来自 Task 实际创建的固定 Judge Execution，binding 来自 prepare_agent_finalization 对原 limit_reached 预约的核验。不构造 Worker DebateTurnBinding，不伪造 active_turn，也不把最后发言当作最终答案。

专用 `FinalizationContextBuilder` 复用 TurnContextBuilder 的共享 Frame、初始答案、首次 REVIEW/证据、完整已提交历史及私有工具组验证。原 `_turns` 和 `_own_history` 保留 Worker 包装入口，下层增加显式来源参数的 `_committed_turns`/`_private_history` 供两种场景共用；未放宽原 Worker 条件。

终局所有下列块都为必需：原问题/格式、两份冻结正式答案、双方最新有效立场、按 turn_no 排序的**全部**本 Debate 发言、首次 Judge 的实质分歧和真实引用、终局目标/输出 Schema、当前有效共享 RAG 及全部来源/别名、当前 Execution 的完整工具组。没有机器可验证的分歧解决字段，因此保守保留全部首次分歧，不根据自然语言声称“已经解决”删掉相反证据；Judge 可以明确表达仍有冲突/不确定性。

每条历史同时核对 ContextItem、completed Execution、agent_message_runs 和 turn receipt；当前/未来/未登记发言拒绝，其他 Debate 发言不选取。其他 Execution（含首次 Judge 与失败终局尝试）的私有轨迹不重放。引用须属于初始答案或当前 Task/Context 最新有效共享 RAG，并通过首次评估的实际输入白名单。失效/替换证据不自动回退旧版本。

`FinalizationRequestBuilderFactory` 只在实际 Scope 创建后装配；每个适配器保存自己的 `FinalizationInputSnapshot`：真实 Scope、final_key、共享业务摘要、请求契约摘要及已完成来源 Execution 摘要。私有事件导致 Context.version 增长不使快照失效，共享资料/来源/绑定变化则拒绝后续请求。最终 validate_request 先核对 Provider 的真实 messages/tools/options/model/max_tokens 预算，再复查快照；Task 包装器还会重新验证执行所有权。仍使用原 Provider/Loop 的完整工具协议校验。

`effective_request_budget` 从旧 Worker 适配器提取为共享函数，原行为不变：使用同一实际请求的 model 和正整数 max_tokens，输出预留取配置和真实上限的较大者。必需资料过长时抛 TokenBudgetExceeded，不静默压缩完整发言、初始答案、分歧或证据。默认 TokenBudget 的容量须按部署模型配置；UTF-8 字节代理/词表估算不是精确 token 或真实 usage。

输出 Schema 来自原 `FinishWithAnswer` 模型，FinalizationOutputContract 复用原安全解析并拒绝 continue。message 是公开裁决摘要，不是隐藏推理；正式 Answer/Task.final_result 只保存已校验的 final_answer 正文。真实提交与准备恢复见[阶段 5 实施报告](DEBATE_ORCHESTRATION_DESIGN.md#16-阶段-5-实际实现终局-judge-原子提交与恢复)。Schema 与事务验证不证明模型裁决的事实正确性。

