# 环境变量说明

后端从自身工作目录读取配置，进程环境优先于文件；APP_ENV决定优先.env.development或.env.production，存在时选该文件，否则.env。新ZIP只含示例，不含配置文件。前端和Compose的.env是另外两份，不能混用。

下表默认值直接读取Settings.model_fields声明，不读取本机配置。表中“配置”指实际部署需替换或依业务启用，不表示Pydantic语法上的必填。

## 后端完整Settings字段


| 变量 | 代码默认 | 用途 | 配置要求 |
|---|---|---|---|
| APP_ENV | development | 运行环境；production拒绝已知弱JWT值，选择同名环境文件 | 可使用默认；按功能启用 |
| APP_NAME | Cultural Heritage QA Backend | API名称 | 可使用默认；按功能启用 |
| APP_VERSION | 0.1.0 | API版本 | 可使用默认；按功能启用 |
| DEBUG | false | 调试配置 | 可使用默认；按功能启用 |
| BACKEND_CORS_ORIGINS | http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080,http://127.0.0.1:8080 | 逗号分隔允许浏览器来源 | 可使用默认；按功能启用 |
| DATABASE_HOST | 127.0.0.1 | MySQL主机 | 部署核对/配置 |
| DATABASE_PORT | 3306 | MySQL端口 | 部署核对/配置 |
| DATABASE_USER | root | MySQL应用账号 | 部署核对/配置 |
| DATABASE_PASSWORD |  | MySQL密码，不打印 | 部署核对/配置 |
| DATABASE_NAME | aijiu | 业务库名 | 部署核对/配置 |
| JWT_SECRET_KEY | change-me | JWT签名密钥，必须本地生成随机值 | 部署核对/配置 |
| JWT_ALGORITHM | HS256 | JWT签名算法 | 可使用默认；按功能启用 |
| JWT_ACCESS_TOKEN_EXPIRE_MINUTES | 60 | 访问令牌有效分钟 | 可使用默认；按功能启用 |
| REDIS_HOST | localhost | Redis主机 | 部署核对/配置 |
| REDIS_PORT | 6379 | Redis端口 | 可使用默认；按功能启用 |
| REDIS_DB | 0 | Redis逻辑数据库 | 可使用默认；按功能启用 |
| REDIS_PASSWORD | <empty> | Redis密码，交付Compose要求非空 | 部署核对/配置 |
| EMBEDDING_BASE_URL | http://127.0.0.1:8001 | Embedding服务根URL（不加/v1） | 部署核对/配置 |
| EMBEDDING_API_KEY | <empty> | 可选Embedding鉴权 | 可使用默认；按功能启用 |
| EMBEDDING_MODEL | qwen3-embedding | 与served-model-name一致 | 部署核对/配置 |
| EMBEDDING_DIMENSION | 1024 | 向量维度，与Milvus和模型一致 | 部署核对/配置 |
| EMBEDDING_BATCH_SIZE | 16 | 文档批大小 | 可使用默认；按功能启用 |
| EMBEDDING_TIMEOUT_SECONDS | 60.0 | 请求超时秒 | 可使用默认；按功能启用 |
| EMBEDDING_QUERY_INSTRUCTION | Given a cultural heritage question, retrieve relevant passages that help answer the question. | 仅query附加的检索指令 | 可使用默认；按功能启用 |
| EMBEDDING_NORMALIZED | true | 描述服务输出；客户端不在此额外归一化 | 可使用默认；按功能启用 |
| RERANKER_BASE_URL | http://127.0.0.1:8002 | Rerank服务根URL（不加/v1） | 部署核对/配置 |
| RERANKER_MODEL | qwen3-reranker | Rerank served alias | 部署核对/配置 |
| RERANKER_TIMEOUT_SECONDS | 60.0 | Rerank超时秒 | 可使用默认；按功能启用 |
| RERANKER_CANDIDATE_TOP_K | 20 | Hybrid候选数，不小于最终top_k | 可使用默认；按功能启用 |
| RERANKER_TOP_K | 5 | 重排默认返回数 | 可使用默认；按功能启用 |
| RAG_MILVUS_URI | http://localhost:19530 | Milvus HTTP URI | 部署核对/配置 |
| RAG_MILVUS_TOKEN | <empty> | 可选Milvus token，不等于MinIO密码 | 可使用默认；按功能启用 |
| RAG_MILVUS_COLLECTION_NAME | heritage_chunks | 集合名，初始化和运行必须一致 | 部署核对/配置 |
| RAG_MILVUS_METRIC_TYPE | COSINE | Dense度量，当前COSINE | 可使用默认；按功能启用 |
| RAG_RETRIEVAL_TOP_K | 3 | 显式Dense/BM25默认数 | 可使用默认；按功能启用 |
| RAG_TASK_RETRIEVAL_TOP_K | 4 | 任务公共上下文数 | 可使用默认；按功能启用 |
| RAG_TASK_AGGREGATOR_AI_ID | <none> | 可选专用聚合Agent ID覆盖；空时从排名中选择启用、URL有效且available或尚未检查的judge并冻结绑定 | 可选覆盖，显式指定更便于演示复现 |
| RAG_TASK_CONSUMER_ENABLED | false | 众包持久队列消费者开关 | 可使用默认；按功能启用 |
| RAG_TASK_POLL_SECONDS | 2.0 | 队列轮询秒 | 可使用默认；按功能启用 |
| RAG_TASK_LEASE_SECONDS | 120 | 租约秒 | 可使用默认；按功能启用 |
| RAG_TASK_MAX_ATTEMPTS | 3 | 最大尝试次数 | 可使用默认；按功能启用 |
| RAG_TASK_RETRY_SECONDS | 10 | 重试间隔秒 | 可使用默认；按功能启用 |
| TAVILY_API_KEY | <empty> | 可选联网搜索Key；空时相关工具禁用 | 可使用默认；按功能启用 |
| TAVILY_TIMEOUT_SECONDS | 30.0 | 搜索超时秒 | 可使用默认；按功能启用 |
| LLM_TIMEOUT_SECONDS | 120.0 | LLM请求超时秒 | 可使用默认；按功能启用 |
| LLM_TOOL_TIMEOUT_SECONDS | 60.0 | LLM工具执行超时秒 | 可使用默认；按功能启用 |
| LLM_HEALTH_TIMEOUT_SECONDS | 30.0 | Agent健康检查超时秒 | 可使用默认；按功能启用 |
| AI_HEALTH_CHECK_ON_STARTUP | true | 启动时一次模型检查，可能付费；示例设false | 可使用默认；按功能启用 |
| HCP_MAD_DEBATE_ENABLED | false | 可选Debate开关 | 可使用默认；按功能启用 |
| HCP_MAD_DEBATE_MAX_ROUNDS | 3 | Debate最大轮数1至16 | 可使用默认；按功能启用 |
| HCP_MAD_DEBATE_POLICY_VERSION | judge-trigger-v1 | 创建任务冻结的策略标识 | 可使用默认；按功能启用 |
| DEEPSEEK_API_KEY | <empty> | 当前唯一实现的bearer凭据引用 | 可使用默认；按功能启用 |
| DEEPSEEK_BASE_URL | https://api.deepseek.com | Settings字段；运行Agent URL取数据库，不自动覆盖 | 可使用默认；按功能启用 |

## LLM配置不是统一环境模型名

运行LLM地址与model_name来自ai_agents记录，注册POST /ais的api_url必须是完整chat/completions URL；type为worker或judge。auth_type=none不加鉴权；bearer当前只支持credential_ref=DEEPSEEK_API_KEY。provider仅标识，不会自动转换凭据或地址。generation_config有白名单，reasoning_effort与部分采样参数有互斥约束。

至少两worker和一judge，健康检查available后使用。RAG_TASK_AGGREGATOR_AI_ID可显式指定已启用Agent；最终源码对未绑定任务会通过公共Agent排名选择可用Judge，绑定后不随排名漂移。不存在统一LLM_MODEL/LLM_BASE_URL环境开关，不能凭空添加。DEEPSEEK_BASE_URL虽声明存在，但不改写数据库Agent地址。

## 前端与根启动器

| 变量 | 默认 | 用途 |
|---|---|---|
| VITE_API_URL | /api | 编译进浏览器的API前缀，不得放密钥 |
| BACKEND_URL | http://127.0.0.1:8000 | Vite开发与preview代理，不是生产网关 |
| BASE_URL | Vite默认/ | Vite内建路由基路径 |
| BACKEND_PORT | 8000 | 根scripts/dev.mjs启动端口 |
| FRONTEND_PORT | 5173 | 根启动器前端端口 |

前端示例文件位于culture-front/Cultural heritage/.env.example。更换VITE变量需重新构建；正式静态服务器配置/api代理和SPA回退。

## Docker Compose配置（deployment/.env）

| 变量 | 示例默认/要求 | 用途 |
|---|---|---|
| MYSQL_DATABASE | heritage_competition | 首次初始化数据库；与后端DATABASE_NAME一致 |
| MYSQL_USER | heritage_app | 应用用户，与DATABASE_USER一致 |
| MYSQL_PASSWORD | 必须替换占位符 | 与后端DATABASE_PASSWORD一致 |
| MYSQL_ROOT_PASSWORD | 必须替换；不交给后端 | MySQL管理员 |
| MYSQL_PORT | 3306 | 主机绑定端口 |
| REDIS_PASSWORD | 必须替换 | 与后端一致 |
| REDIS_PORT | 6379 | 主机绑定端口 |
| MINIO_ROOT_USER | 必须替换 | MinIO及Milvus对象存储凭据 |
| MINIO_ROOT_PASSWORD | 必须替换 | 同上，不是RAG_MILVUS_TOKEN |
| MILVUS_PORT | 19530 | 主机Milvus端口 |
| MILVUS_HEALTH_PORT | 9091 | 主机Milvus健康端口 |

Compose内部ETCD_ENDPOINTS=etcd:2379、MINIO_ADDRESS=minio:9000、MINIO_ACCESS_KEY_ID/SECRET_ACCESS_KEY由上述配置注入，MQ_TYPE=woodpecker。ETCD压缩、配额和快照参数在compose.yml固定。无须向浏览器提供任何存储密码。新命名卷首次导入SQL，既有卷不自动迁移。

## 模型脚本环境

| 变量 | 默认 | 用途 |
|---|---|---|
| PROJECT_MODELS_DIR | 下载脚本model_weights；启动脚本$HOME/models | 建议显式统一设置为$HOME/models |
| EMBEDDING_MODEL_DIR | PROJECT_MODELS_DIR/Qwen3-Embedding-0.6B | 直接指定现有Embedding模型目录 |
| RERANKER_MODEL_DIR | PROJECT_MODELS_DIR/Qwen3-Reranker-0.6B | Rerank目录 |
| MODEL_HOST | 127.0.0.1 | 服务绑定，确需0.0.0.0时加防火墙限制 |
| EMBEDDING_MODEL | qwen3-embedding | 模型服务别名，后端同名变量需一致 |
| RERANKER_MODEL | qwen3-reranker | 同上 |
| EMBEDDING_PORT / RERANKER_PORT | 8001 / 8002 | 服务端口 |
| EMBEDDING_MAX_MODEL_LEN / RERANKER_MAX_MODEL_LEN | 8192 / 4096 | 上下文上限 |
| EMBEDDING_GPU_MEMORY_UTILIZATION / RERANKER_GPU_MEMORY_UTILIZATION | 0.35 / 0.35 | 两进程各自GPU预算 |
| RERANKER_CHAT_TEMPLATE | 随脚本的qwen3_reranker.jinja | 模板路径 |
| VLLM_WSL2_ENABLE_PIN_MEMORY | 1 | 保留已运行WSL基线开关；不是跨版本保证 |

模型启动脚本不会自动读取后端.env，需在各WSL终端export对应变量。下载固定SHA，来源见model_sources.json；Hugging Face自身的网络/缓存变量按其工具文档配置，不是本项目Settings。

## 测试工具变量

浏览器测试使用FRONTEND_URL、PLAYWRIGHT_CHANNEL（可选chrome）、PLAYWRIGHT_NODE_MODULES（可选外部依赖目录）、PYTHON（可选后端解释器）。默认本地依赖通过npm ci安装，外部路径仅是审查环境便利参数，不写死个人路径。测试专用crowd_server使用临时SQLite，不能作为生产启动入口。

## 网络填写方式

- 当前方案：后端在主机运行，数据库与模型均通过127.0.0.1及发布端口访问。
- 后端若放Docker：127.0.0.1指容器自身；同一Compose网络用mysql、redis、milvus服务名，访问主机模型可用可达网关（Docker Desktop常为host.docker.internal）。本轮未验收后端容器部署。
- Windows至WSL优先localhost转发，失败再检查WSL网络与防火墙；不要固化临时WSL IP。
- BACKEND_CORS_ORIGINS填写浏览器实际origin，含协议和端口，不是数据库或容器服务名。
- APP_ENV=production拒绝已知弱JWT值，但不能替代完整公网安全工程。Redis故障应作为部署失败排查，不以开发内存降级视为成功。

示例密码都是占位符。随机密码、Key、JWT、真实用户资料只留在本地受保护配置/存储，ZIP和Git均不能携带；历史已暴露凭据需在服务商侧轮换。
