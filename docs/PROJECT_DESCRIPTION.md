# 项目说明

审查日期：2026-10-23。以实际源码与验证为准，不把历史设计文档当成功能证据。

## 背景与目标

文化遗产资料分散、回答来源难以追溯，单次模型回答可能忽略事实冲突。本项目提供检索、多 Agent 回答和人工众包整理平台，适用于课堂、文化资料整理和受控比赛演示。生成内容需人工核验，不等同于权威文物鉴定或保护决策。

## 已实现功能

1. 账户注册、bcrypt 密码哈希、JWT 登录/退出及个人任务查询。
2. HCP-MAD：选择两个可用 worker 与 judge，持久化上下文、执行记录与正式结果，可选有限轮 debate。
3. 众包 RAG：发布公开任务、分页发现、接取、不可变回答、个人发布/接取列表、正式知识、入库回执和有限轮询。
4. TXT/PDF/DOCX 解析与切片，Embedding、Milvus Hybrid 检索、Rerank及临时上传上下文。
5. Agent 注册、列表与健康检查；provider、model_name、credential_ref、生成参数保存在记录中，Key 从环境解析。
6. 保留 Dense、BM25、Hybrid、Hybrid+Rerank 评测代码，未修改既有评测结果。

实际页面有登录/注册、仪表盘、系统问答、任务大厅、发布任务、我的发布、我的接取、详情、Agent 管理和个人资料。HCP 历史主要恢复浏览器已知任务 ID，未实现跨设备完整 HCP 历史列表。Agent 管理页面不代表已有管理员权限体系。

## 技术栈

前端 Vue3、TypeScript、Vue Router、Pinia、Axios、Element Plus、Vite8，使用 npm 锁文件。后端 Python3.11、FastAPI、Pydantic Settings、SQLAlchemy2、PyMySQL、Redis、HTTPX、bcrypt、JWT。MySQL8.0.41 保存19表业务事实，Redis保存会话/注销，Milvus2.6.22依赖etcd/MinIO。

本地模型 Qwen3-Embedding-0.6B、Qwen3-Reranker-0.6B，WSL2/Conda/vLLM0.29.0，服务别名 qwen3-embedding、qwen3-reranker。外部 LLM 使用 Agent 的 OpenAI兼容 chat/completions 地址；当前 bearer 凭证引用仅实现 DEEPSEEK_API_KEY。

## HCP-MAD 工程化实现

创建任务时冻结策略与上下文，按可用状态和评分选择 worker；回答持久化后由 judge 形成正式结果。Debate默认关闭，启用后记录策略版本并限制轮数。上下文项表达公开协议状态，不承诺展示模型隐藏思维。

prepare/commit 边界、终态校验和持久执行记录用于处理重入与异常。未知模型结果不被当作成功；保存次数、阶段、错误与正式输出。测试覆盖多个恢复场景，但不构成第三方API稳定性或无条件 exactly-once 保证。

## 众包知识生产

发布者创建问题与人数阈值；其他登录用户发现、接取并提交不可编辑回答。参与和回答受唯一性约束，最后一份达标回答在事务内产生持久 job。消费者使用专门聚合 Agent 整理正式知识，再复用解析、切片、向量链路写入Milvus，保存文档ID和片段回执。

租约、generation fencing、唯一任务阶段、预存模型文本和确定性入库计划控制重试。未知外部调用结果需显式确认。消费者默认关闭。最终源码支持未绑定任务从公共Agent排名选取符合条件的Judge并冻结绑定；显式聚合ID为覆盖。没有可用Agent或consumer未启用时不会自动得到最终知识。

## RAG 检索

默认在线路径为 Dense HNSW/COSINE 与 Milvus原生BM25，经RRF(k=60)融合后Rerank，再构造上下文。查询向量附加检索指令，文档向量不附加，当前1024维。Rerank调用/v1/rerank，读取index和relevance_score，校验索引、重复项及有限值。

技术特色包括可追溯执行、正式结果与入库回执分离、安全查询DTO、独立HCP/众包存储、持久恢复边界和空库部署工具；未据此虚构准确率或性能提升。

## 局限

外部LLM依赖网络、API Key和费用；Tavily为可选搜索。CPU环境不能直接复现已测试的双vLLM GPU配置。管理员隔离、TLS和公网运维闭环未完成，前端依赖有审计风险。完整验证边界见DELIVERY_CHECKLIST.md。
