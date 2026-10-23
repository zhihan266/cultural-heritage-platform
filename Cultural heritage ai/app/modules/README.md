# 业务模块导航

当前目录按业务能力组织：account 管理登录和会话；agent 管理 AI 注册、健康和模型调用；task 管理任务、回答、聚合、评分与审核；tool 管理工具调用；heritage 提供结构化数据查询；rag 提供资料入库、检索和上下文。

## RAG 边界

- domain：stdlib 数据模型及 Parser / EmbeddingProvider / Retrieval / Reranker / AnswerGenerator / VectorStore 契约。
- application：依赖上述抽象进行预处理、入库、候选召回、精排和答案生成。
- infrastructure：解析器、模型 HTTP、Milvus 和业务 Agent 生成适配器。
- composition.py：构造对象；build_rag_retriever 决定默认公共策略 Hybrid + RRF + Rerank。
- api/dependencies.py：使用 composition，管理请求内 HTTP / Milvus 客户端。
- task_context.py：使用同一 composition，管理任务内客户端，业务编排由 RagContextService 负责。

Dense-only builder 仅用于显式基线实验。附件使用内存余弦检索，公共部分仍经过默认精排策略。RagAnswerService 注入 AnswerGenerator，实际实现 AgentAnswerGenerator 调用数据库登记的业务 AI；不再另有未接入的直接生成适配器。

Reranker 配置统一属于 Settings；ai_agents 仍承担业务 AI 配置，没有新建 Reranker 配置表。临时文件、数据库及外部 SDK 不进入 RAG domain。

启动、服务配置、当前限制和验证命令见 [根 README](../../README.md)，本轮变更见 [发布报告](../../docs/GITHUB_RELEASE_REPORT.md)。其他模块保留现有内部结构，后续根据真实改动再细化，避免形式分层。
