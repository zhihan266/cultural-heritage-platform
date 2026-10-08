# Cultural Heritage Multi-Agent System

面向文化遗产知识问答的 FastAPI 后端：用户创建任务、选择多个业务 AI，系统共享检索资料，让各 AI 通过模型与工具生成候选答案，再由聚合 AI 汇总、评分并保存结果。人工工人也可以领取任务和提交答案。仓库不包含前端。

状态核对：**2026-10-08**。默认公共检索已统一为 **Hybrid → RRF → Rerank**，HTTP 和 Task 使用相同装配入口。离线测试覆盖依赖装配、检索、入库、账号和任务契约；本轮没有运行真实 MySQL / Redis / Milvus / 模型服务的端到端联调。项目是可配置的工程原型，尚未完成生产权限、任务恢复和质量评估闭环。详细变更见 [发布前报告](docs/GITHUB_RELEASE_REPORT.md)。

## Architecture

```text
User / Worker
    ↓
FastAPI — account / agent / task / rag
    ↓
TaskOrchestrator（条件更新领取任务）
    ├─ NORMAL：直接执行业务 AI
    └─ RAG：一次构造共享 Context
              ↓
         并发 Tool Calling Agents
              ↓
         候选答案分别落库
              ↓
         Aggregator → Scoring → Final Answer
              ↓
         发布者最终审核 / 信誉分更新
```

```text
Document → Parse → Clean → Split → Embedding → Milvus
Query → Query Embedding
           ↓
       Hybrid Retrieval
       ├─ Dense / HNSW / COSINE
       └─ Milvus jieba analyzer → native BM25 / sparse index
           ↓
       RRF → candidate pool → Reranker → final TopK
           ↓
       Context → LLM / Tool Calling → Answer
```

RRF 根据排名融合两路结果，避免直接相加不同尺度的分数；Reranker 在较大候选池上精排。BM25 sparse vectors 由 Milvus 生成，Python 不维护另一套稀疏模型。

## 核心能力

| 能力 | 当前行为 | 边界 |
| --- | --- | --- |
| 账号与会话 | 密码哈希、JWT、Redis 当前 token 与退出黑名单 | 部分接口未鉴权；开发环境 Redis 故障会固定切到进程内存 |
| 公共 RAG | TXT / PDF / DOCX 入库，Hybrid + RRF + Rerank，来源返回 | 无自动 schema migration、全文档版本管理和引用核验 |
| 任务附件 | 按任务保存文件，临时向量只保留在处理内存中 | 未实现成功后自动清理 / TTL |
| Tool Calling | JSON Schema 参数校验，全部 tool calls 执行和消息回填，最多 5 轮 | 工具异常终止当前 Agent；无持久 trace |
| 工具 | 正式名称查询文化遗产；可选 Tavily 搜索与网页提取 | 空业务库返回 found=false；没有内置文化遗产 seed |
| 多 Agent / 聚合 | 中央编排多个业务 AI，聚合模型生成结果，再评价候选答案 | 无 Agent 间辩论；评分不等于加权投票 |
| Evaluation | 40 条文档级标注问题、Recall@K / MRR / latency、分类汇总 | 历史报告只有 Dense；本轮没有新实验结果 |
| HCP-MAD | **Planned / In Progress** | 尚未实现动态选择、讨论与完整算法 |

## 项目结构

```text
app/
├─ main.py                     路由与应用生命周期
├─ core/                       Settings、日志、Redis
├─ db/                         SQLAlchemy 会话与业务表注册
└─ modules/
   ├─ account/                 登录、JWT、会话
   ├─ agent/                   业务 AI 配置、健康检查、LLM 协议
   ├─ task/                    任务、候选答案、聚合、评分与审核
   ├─ tool/                    工具契约、参数校验与执行
   ├─ heritage/                结构化文化遗产查询
   └─ rag/
      ├─ api/                  HTTP schema / router / dependency
      ├─ application/          入库、检索、上下文与问答编排
      ├─ domain/               stdlib 数据模型与 ABC 契约
      ├─ infrastructure/       Parser、HTTP providers、Milvus
      ├─ composition.py        创建具体实现并注入业务层
      └─ task_context.py       Task 的薄适配入口及资源生命周期
evaluation/                    数据集、指标、策略 runner 与历史报告
tests/                         离线回归测试
scripts/                       明确手动运行的集成检查和数据准备
data/                          小型解析 fixture 与检索评估文档
migrations/                    历史 SQL 迁移，按已有数据库情况使用
docs/                          当前报告和历史设计记录
```

RAG application 依赖 domain 契约，基础设施只在 composition 中组装。HTTP dependencies 管理请求内的 HTTP/Milvus 客户端；Task 适配器管理任务内客户端。同步 SDK、解析和入库通过工作线程调用。其他业务模块保留现有目录，避免为分层增加并行实现。

## Quick Start

### 1. 安装后端

使用 **Python 3.11**，在项目根目录执行。以下是 PowerShell 命令；Linux/macOS 用虚拟环境的 `bin/python` 替换路径。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

修改本地 `.env` 中的数据库密码和 JWT 密钥。不要把实际配置提交到 Git。

### 2. MySQL / Redis / Milvus

- MySQL 8.x：先创建数据库及有建表权限的本地用户。应用启动会对缺失的业务表执行 `create_all`，不会迁移已有表。
- Redis：会话校验需要可访问的 Redis。生产环境不使用内存降级；开发降级不适合多进程。
- Milvus：公共 RAG 需要支持 full-text BM25 / jieba 的服务。建议使用 2.6 系列并按服务器版本核对客户端；仓库固定 `pymilvus==2.6.17`。按 [Milvus 官方部署文档](https://milvus.io/docs/v2.6.x/install_standalone-docker.md) 安装独立服务；不要把运行卷提交到仓库。BM25 的服务能力见 [官方全文检索文档](https://milvus.io/docs/v2.6.x/full-text-search.md)。

MySQL 示例（管理员连接中执行，密码自行替换）：

```sql
CREATE DATABASE aijiu CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'cultural_heritage'@'localhost' IDENTIFIED BY 'replace-with-your-local-password';
GRANT ALL PRIVILEGES ON aijiu.* TO 'cultural_heritage'@'localhost';
```

已有数据库先备份并核对表结构；`migrations/` 是历史迁移，不是全自动升级工具。本轮没有改动业务表定义或执行数据库迁移。

### 3. Embedding / Reranker

模型服务独立部署，不属于后端依赖安装。以下命令在已经安装 vLLM 的模型服务器上执行，模型权重和所需硬件自行准备。

Embedding 使用服务名 `qwen3-embedding`；Qwen3-Embedding-0.6B 的原生维度为 1024，匹配示例配置。参见 [模型发布方说明](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) 和 [vLLM embedding 文档](https://docs.vllm.ai/en/stable/models/pooling_models/embed/)。

```bash
vllm serve Qwen/Qwen3-Embedding-0.6B --runner pooling --served-model-name qwen3-embedding --host 127.0.0.1 --port 8001
```

Qwen3 Reranker 需要 sequence-classification overrides 和匹配的模板。将对应 vLLM 版本中的 [官方模板](https://github.com/vllm-project/vllm/blob/main/examples/pooling/score/template/qwen3_reranker.jinja) 保存为模型服务器当前目录的 `qwen3_reranker.jinja`，再执行：

```bash
vllm serve Qwen/Qwen3-Reranker-0.6B --runner pooling --host 127.0.0.1 --port 8002 --hf_overrides '{"architectures":["Qwen3ForSequenceClassification"],"classifier_from_token":["no","yes"],"is_original_qwen3_reranker":true}' --chat-template qwen3_reranker.jinja
```

启动参数以所装版本为准，参考 [vLLM 官方 scoring 示例](https://docs.vllm.ai/en/stable/examples/pooling/score/)。后端调用 `POST /v1/rerank`，发送 `model/query/documents/top_n`，解析 `results[].index/relevance_score`；参见 [官方 rerank 协议](https://docs.vllm.ai/en/stable/models/pooling_models/scoring/)。

服务启动后，在后端环境执行：

```powershell
.\.venv\Scripts\python.exe -m scripts.test_milvus_connection
.\.venv\Scripts\python.exe -m scripts.test_vllm_embedding
.\.venv\Scripts\python.exe -m scripts.test_vllm_rerank
```

这些是**真实外部服务检查**，普通 pytest 不会代替它们。

### 4. LLM / 注册业务 AI

本项目调用无需密钥的本地 OpenAI-compatible `/v1/chat/completions`。可用本地 Ollama；先安装并启动服务，再拉取支持工具调用的模型，例如：

```bash
ollama serve
# 在另一个终端执行
ollama pull qwen3:8b
```

Ollama 的本地兼容接口和工具支持见 [官方说明](https://docs.ollama.com/api/openai-compatibility)。模型名必须等于服务实际接受的名字；还需能处理本项目的工具消息和评分提示词。

### 5. 启动 API 并建立最小业务流程

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

打开 [Swagger](http://127.0.0.1:8000/docs)。启动需要 MySQL 可连接；Embedding / Reranker / Milvus 在使用 RAG 时需要可连接。`/health` 仅表示应用存活，不检测所有依赖。

在 Swagger 中：

1. `POST /users/register` 和 `POST /users/login`，用返回的 Bearer token 授权。
2. `POST /ais` 注册业务 AI；示例：
   `{"name":"qwen3:8b","api_url":"http://127.0.0.1:11434/v1/chat/completions","type":"business","initial_score":1}`。
3. 再注册一个 `type="aggregator"` 的 AI，名字和地址可以与业务模型相同。
4. 对两个返回的 AI ID 调用 `POST /ais/{ai_id}/health-check`，确认 available=true。
5. `POST /admin/rag/documents` 上传公共资料；或用下文脚本索引评估文档。
6. `POST /rag/retrieve` 传 `{"query":"天坛的用途是什么？","top_k":5}`；`POST /rag/answer` 再带业务 `ai_id`。
7. `POST /users/tasks/rag` 的 multipart `payload` 填 JSON：`{"question":"天坛的用途是什么？","answer_format":"普通文本","ai_ids":["替换为业务AI ID"],"worker_count":0,"metadata":{}}`；附件可选。轮询 `GET /users/tasks/{task_id}` 查看结果。

管理员上传、部分 AI 和任务处理接口尚未统一鉴权。本地试运行和公开展示可以按上述流程进行；部署到公网前先补权限。

## Configuration

`.env.example` 与 [Settings](app/core/config.py) 同步，使用实际键名。环境变量优先；若 `.env.development` 或 `.env.production` 等选定文件存在，则优先读取该文件，否则读取 `.env`。通过系统环境变量 `APP_ENV` 选择环境；新 clone 默认 development。

| 配置组 | 用途 |
| --- | --- |
| DATABASE_HOST / PORT / USER / PASSWORD / NAME | MySQL 连接，密码在生成 URL 时编码 |
| JWT_SECRET_KEY / ALGORITHM / ACCESS_TOKEN_EXPIRE_MINUTES | 原 JWT 语义；production 拒绝已知占位密钥 |
| REDIS_HOST / PORT / DB / PASSWORD | token 与退出黑名单 |
| RAG_MILVUS_URI / TOKEN / COLLECTION_NAME / METRIC_TYPE | 公共向量库连接，默认 COSINE |
| EMBEDDING_BASE_URL / API_KEY / MODEL / DIMENSION / BATCH_SIZE | Embedding 模型及批次；BASE_URL 不带 /v1 |
| EMBEDDING_QUERY_INSTRUCTION / NORMALIZED / TIMEOUT_SECONDS | 查询指令、服务输出声明和超时 |
| RERANKER_BASE_URL / MODEL / TIMEOUT_SECONDS / CANDIDATE_TOP_K / TOP_K | 单一基础设施 Reranker，默认 20 候选 / 5 最终结果 |
| RAG_TASK_RETRIEVAL_TOP_K | 无附件 Task 默认取 4 条 |
| RAG_RETRIEVAL_TOP_K | 显式 Dense 基线默认值；公开 API 仍按请求 top_k |
| LLM_TIMEOUT_SECONDS / LLM_TOOL_TIMEOUT_SECONDS / LLM_HEALTH_TIMEOUT_SECONDS | 模型生成、工具调用、健康检查超时 |
| TAVILY_API_KEY / TIMEOUT_SECONDS | 可选 Web 工具；密钥为空时不注册 |

表中组内省略的重复前缀以 `.env.example` 为准。业务 LLM 名字、地址和角色写入 `ai_agents`，通过 `POST /ais` 注册；不存在另一套 LLM_BASE_URL / LLM_MODEL 配置。Embedding 密钥、Milvus token、Redis 密码和 Tavily 密钥只从本地配置读取。

## RAG Pipeline

- document_id 仍为原文件内容的 SHA-256；chunk_id 保留原文档、位置和内容生成规则。重复索引使用相同 ID upsert。
- 默认切分参数保持 800 / overlap 120 / 最小 100 字符；入库只解析一次。PDF 默认提取文本层，OCR 默认未启用；DOCX 当前提取段落。
- Dense 索引保持 HNSW M=16 / efConstruction=200，搜索 ef=64；稀疏索引为 SPARSE_INVERTED_INDEX / BM25 / DAAT_MAXSCORE；RRF k=60。
- 候选池至少为 20 且不小于最终 top_k。默认公共检索经过 Rerank，最终数量不超过请求 top_k。
- 返回的 `score` 保留原检索分数，默认是 RRF 分数。**结果按 Reranker relevance 排序**，内部 metadata 保存独立 `reranker_score`；两个分数不能解释为相同指标。公开响应字段保持原样。
- 无附件 Task 使用同一公共策略取 4 条。有附件时公共 3 条 + 临时余弦 2 条；两部分分别生成 query embedding，再按来源标记组合，不跨来源直接比较分数。
- Task 的上下文只构造一次，共享给业务 AI、聚合与评分；RAG 异常继续采用原有 fail-open，降级为空上下文。直接 RAG 接口的依赖失败会报错。
- Milvus 当前保存正文及基本来源，页码等完整 metadata 尚未持久化，来源列表不代表逐句引用已经核验。

旧 Dense-only collection 缺少 sparse/BM25 字段时，默认检索会明确报错，**不会自动删除 collection**。优先在 `.env` 设置新的 collection 名并重新索引。确需删除旧开发 collection 时，先备份并核对配置，再显式使用 `scripts.reset_rag_collection --confirm <配置中的collection名>`；production 下该脚本拒绝执行。本轮没有运行删除操作。

## Multi-Agent Pipeline

任务提交后通过进程内 FastAPI BackgroundTasks 执行。条件更新保证同一 waiting 任务只有一个领取者；每个并发 AI 使用独立会话保存答案。至少有一条有效落库答案后进入聚合、评分，全部失败则标记 failed。评分校验重复 ID、覆盖范围与数值，错误进入失败处理。

当前自动流程不等待所有人工工人；纯人工任务不触发自动 AI 编排，需要显式完成流程。评分更新与最终审核尚未形成完整幂等事务。没有持久队列、重启恢复、Agent 间讨论或持续轨迹。**HCP-MAD 保留为计划能力。**

## Evaluation

评估文档共 30 份：`data/rag_eval/` 的 29 份 TXT 加 `data/test_upload/temple_of_heaven.txt`。40 条标注问题的 relevant_document_ids 均匹配这些文件的内容哈希；数据、指标算法和原有报告保留。

历史 [Dense 基线](evaluation/reports/baseline/dense_v1.json)：

| 指标 | 历史值 |
| --- | ---: |
| Recall@1 | 0.9396 |
| Recall@3 | 0.9938 |
| Recall@5 | 1.0000 |
| MRR（截断至返回结果） | 1.0000 |
| 平均检索耗时 | 71.98 ms |

这是小型文档级检索数据集上的历史结果，缺少当时完整模型/硬件/服务版本记录，不能代表答案正确率、当前 Hybrid+Rerank 效果或生产性能。当前 runner 保持先取 5 个 chunk、再按文档 ID 去重的指标口径；重复 chunk 可能导致实际返回的独立文档不足 5 个。

真实评估需要外部服务：

```powershell
.\.venv\Scripts\python.exe -m scripts.index_rag_eval_documents
.\.venv\Scripts\python.exe -m evaluation.runners.rag_retrieval_runner --strategy dense
.\.venv\Scripts\python.exe -m evaluation.runners.rag_retrieval_runner --strategy hybrid
.\.venv\Scripts\python.exe -m evaluation.runners.rag_retrieval_runner --strategy hybrid_rerank
```

新报告在 `evaluation/reports/` 使用编号与时间戳保存，禁止覆盖历史文件；记录数据集 hash、模型、维度和策略参数，不导出服务地址与密钥。本轮未执行上述评估、没有增加实验数字。

其他手动入口：

```powershell
.\.venv\Scripts\python.exe -m scripts.test_tool_agent_loop --ai-id <注册得到的AI-ID> "查询数据库中故宫的信息"
.\.venv\Scripts\python.exe -m scripts.test_heritage_detail_tool "故宫"
.\.venv\Scripts\python.exe -m scripts.export_rag_document_catalog --output evaluation/reports/catalog_local.json
```

## Verification / GitHub 发布

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m compileall app
.\.venv\Scripts\python.exe -c "from app.main import app; print('app import ok')"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check app tests scripts evaluation
.\.venv\Scripts\python.exe -m mypy app scripts evaluation
.\.venv\Scripts\python.exe -m scripts.check_publication_safety
.\.venv\Scripts\python.exe -m scripts.check_publication_safety --index
.\.venv\Scripts\python.exe -m scripts.check_publication_safety --history
```

pytest 默认只收集 `tests/`，阻止真实 Milvus 连接，并隔离应用启动的外部依赖；`scripts/test_*.py` 是显式手动检查。离线测试不证明真实服务可用。

`.env*`（除 example）、环境目录、缓存、IDE、日志、临时上传、数据库 dump 和模型/服务运行卷都被忽略；评估数据和测试 fixture 保留。扫描输出只显示位置与问题类型，不回显敏感值。扫描仅检查高置信特征，提交前还要审阅完整 diff 和文档元数据。

**已知发布阻碍：已有 Git 历史的四个 .env.production blob 含私有服务器地址。** 当前文件已取消跟踪，本地配置保留；工作区清理不能擦除历史。公开现有历史前，应在备份副本中进行历史清理并重新扫描，或将审核后的当前快照发布到新的无旧历史仓库。若历史还含已用凭据，需要轮换。不要直接公开现有历史；本轮没有重写历史、提交或推送。

## Roadmap

- 统一鉴权和资源所有权检查；限制 AI 注册与管理员入库权限。
- 持久任务队列、失败原因、重启恢复、幂等完成与审核。
- 临时文件 TTL，文档版本与删除管理，完整来源 metadata。
- 真实服务端到端验证；Dense / Hybrid / Hybrid+Rerank 对照和答案质量评估。
- HCP-MAD：动态 Agent 选择、讨论与对照实验（Planned / In Progress）。
