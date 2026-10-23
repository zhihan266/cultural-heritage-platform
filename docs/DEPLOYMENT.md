# 完整部署教程

所有命令从解压后的项目根目录开始；示例为 Windows PowerShell + WSL2 Ubuntu。Linux 可将 .venv/Scripts/python.exe 换为 .venv/bin/python，使用 python3.11 -m venv 创建后端环境。不要把本机已有数据库当作新环境清空。

## 0. 新Windows机器安装前置工具

先从官方安装[Python3.11 x64](https://docs.python.org/3.11/using/windows.html)、[Node.js](https://nodejs.org/en/download)（满足下表引擎）、[Git](https://git-scm.com/downloads)和[Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/)。Docker选择Linux containers/WSL2后端并启动应用；安装器和驱动属于操作者安装步骤，本轮未重装验证。

管理员PowerShell，按[微软WSL说明](https://learn.microsoft.com/en-us/windows/wsl/install)：

```powershell
wsl --install -d Ubuntu-24.04
# 按系统提示重启、完成Ubuntu首次用户名设置，再检查：
wsl --list --verbose
py -3.11 --version
node --version
npm --version
git --version
docker version
docker compose version
```

成功判定：Ubuntu的VERSION为2，Python3.11，Node满足引擎，docker version同时显示Client和Server。NVIDIA驱动安装在Windows主机并确认WSL中nvidia-smi可用；不在WSL里盲装另一份显示驱动。

尚无Conda时，WSL终端可按[Miniforge官方安装说明](https://github.com/conda-forge/miniforge)安装（已有Conda则跳过，避免覆盖）：

```bash
sudo apt-get update
sudo apt-get install -y curl ca-certificates
curl -fL -o Miniforge3.sh https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh
# 核对官方发布页的SHA256后运行交互式安装，接受自己的安装目录并初始化bash：
bash Miniforge3.sh
# 关闭并重新打开WSL终端：
conda --version
```

本机测得Conda26.7.1。Miniforge提供Conda工具，模型包仍按本项目固定版本安装；安装器的latest不是模型权重版本。部署前记录实际下载版本和SHA256，不替换已有Conda环境。

## 1. 环境与验证基线

| 部件 | 要求或实际验证 |
|---|---|
| 主机 | Windows + WSL2 Ubuntu24.04；Linux原生可按相同接口部署，但本轮未重装验证 |
| 后端 | Python3.11；requirements.txt精确版本，开发检查用requirements-dev.txt |
| 前端 | package.json要求Node ^20.19或>=22.12；本次Node22.13.0、npm10.9.2 |
| Docker | 本机Docker29.8.0、Compose5.5.1；需Docker daemon可访问 |
| 数据库 | MySQL8.0.41，Redis7.4.2，Milvus2.6.22+etcd3.5.25+MinIO指定镜像 |
| 模型环境 | WSL Python3.11.16、vLLM0.29.0、torch2.13.0+cu130、CUDA13.0、transformers5.17.0 |
| GPU | 实测RTX4060 Laptop 8GB、驱动610.47、计算能力8.9；不是所有8GB卡的保证 |

建议主机内存16GB以上并预留Docker和两个模型空间（容量建议，不是性能测量）。两个模型各约1.2GB权重；gpu-memory-utilization均0.35，合计预算70%，8GB约5.6GB。运行时还需激活、CUDA上下文等开销，本轮短输入双服务实际推理通过，未做8192长上下文并发/压力测试。显存不足先缩短max-model-len、降低并发或分开运行，不要更换模型以伪造兼容。

已验证安装版本是复现基线，并非宣称唯一受支持版本。新GPU/驱动组合先核对[vLLM GPU安装说明](https://docs.vllm.ai/en/stable/getting_started/installation/gpu/)。不要在Windows Python环境安装vLLM。

## 2. 安装后端和前端依赖

根目录：

```powershell
cd 'Cultural heritage ai'
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip check
Copy-Item .env.example .env
cd ..
cd 'culture-front/Cultural heritage'
npm ci
Copy-Item .env.example .env
npm run build
cd ../..
```

成功判定：pip check无冲突、npm ci退出0、前端生成dist。Windows Python3.11若需连同传递依赖固定，可改用pip install -r requirements-lock-windows-py311.txt；该清单来自本轮干净解压后安装并执行全量测试的环境，Linux请使用requirements-dev.txt并单独记录平台锁。npm audit风险另见审查报告；安装成功不等于安全审计通过。本项目根目录package.json无额外依赖，仅启动脚本。

## 3. 设置私密配置

```powershell
Copy-Item deployment/.env.example deployment/.env
& '.\Cultural heritage ai\.venv\Scripts\python.exe' -c "import secrets; print(secrets.token_urlsafe(48))"
```

最后命令只在本地生成随机值，用于JWT或独立服务密码，不提交到版本库。编辑deployment/.env，替换MySQL应用/管理员、Redis、MinIO占位密码；编辑后端.env，将DATABASE_NAME/USER/PASSWORD、REDIS_PASSWORD与之对应。JWT使用独立随机值。模型别名必须为qwen3-embedding/qwen3-reranker。前端不保存任何Key。

完整变量见ENVIRONMENT.md。后端若已有.env.development或.env.production会优先读取相应文件；工作区升级要核对该优先级，新ZIP不会包含这些文件。不要把别人的配置文件直接拷贝进新部署。

## 4. 启动MySQL、Redis、Milvus

先检查3306/6379/19530/9091有无已有服务；若冲突，在deployment/.env改端口，同时更新后端配置。新Compose项目有独立命名卷，所有发布端口默认127.0.0.1。

```powershell
docker compose --env-file deployment/.env -f deployment/compose.yml config --quiet
docker compose --env-file deployment/.env -f deployment/compose.yml up -d
docker compose --env-file deployment/.env -f deployment/compose.yml ps
```

成功判定：五个服务healthy；Milvus首次启动可需数分钟。config --quiet不打印展开后的密码；不要分享完整config输出。Windows可直接调用Docker，无须启用WSL发行版Docker集成；WSL找不到docker时使用PowerShell终端。

MySQL**首次新卷**自动挂载并导入deployment/sql/mysql_schema.sql，完整19表，没有账户、任务或Key数据。已有非空卷不会自动补表。不要运行down -v清空既有数据；导入失败先保留日志与卷，按报告处理。

核对表结构（根目录）：

```powershell
& '.\Cultural heritage ai\.venv\Scripts\python.exe' deployment/database.py check
```

预期missing_tables为空、tables为空。新库SQL完全来自现有ORM，按依赖顺序创建，保留历史映射表；不是把历史迁移逐个重复执行。

若使用现有独立MySQL安装而不是Compose：先在后端.env填写有CREATE权限的本地账户，为**专门的新数据库**执行：

```powershell
& '.\Cultural heritage ai\.venv\Scripts\python.exe' deployment/database.py init --database heritage_competition --create-database --apply
```

脚本拒绝有任何表的目标；不会drop或覆盖。填好后端DATABASE_NAME后再check。也可在mysql客户端先选择新空库，再source deployment/sql/mysql_schema.sql。MySQL DDL非全事务回滚，若中途失败不要盲目重跑，保留失败信息并选另一个新验证库或人工修复。

初次审查时后端sql/createsql.sql没有建表语句；用户随后补充了完整19表导出，用户随后又将工作区文件命名为sql/createtable.sql，其与deployment/sql/user_export_reference.sql原文一致。导出包含固定CREATE/USE aijiu_dev，交付中保留作对照。不要直接在未知主机导入它；其缺失外键和可选修复见AUDIT_REPORT.md与existing_database_optional_fk.sql。

## 5. WSL/Conda与模型下载

在WSL终端，把项目移动到自己的路径；下例进入Windows项目挂载目录，不固定任何人的home。conda需先按自身安装位置初始化并可用。

```bash
cd /mnt/c/Code/culture-project
conda create -n heritage-vllm python=3.11 -y
conda activate heritage-vllm
python -m pip install -r deployment/models/requirements-vllm.txt
python -m pip check
nvidia-smi
export PROJECT_MODELS_DIR="$HOME/models"
python deployment/models/download_models.py --models-dir "$PROJECT_MODELS_DIR" --dry-run
# 确认下载地址和空间后，由操作者明确执行：
python deployment/models/download_models.py --models-dir "$PROJECT_MODELS_DIR"
```

ZIP不含权重，打包从不触发下载。本轮使用已有权重，未下载模型。脚本固定原模型仓库与revision，可中断后重跑。需网络访问Hugging Face；镜像源由操作者自行核实，不能混入不同模型。已下载的模型可直接通过EMBEDDING_MODEL_DIR/RERANKER_MODEL_DIR指定，无需重复下载。

[Embedding模型卡](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)与[Reranker模型卡](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B)给出来源与Apache-2.0授权。确切revision见model_sources.json，随包模板来源与许可证见THIRD_PARTY_NOTICES.md。

## 6. 两个终端独立启动

终端A，WSL项目根目录：

```bash
conda activate heritage-vllm
export PROJECT_MODELS_DIR="$HOME/models"
bash deployment/models/start_embedding.sh
```

终端B，同目录：

```bash
conda activate heritage-vllm
export PROJECT_MODELS_DIR="$HOME/models"
bash deployment/models/start_reranker.sh
```

两终端保持运行；也可由操作者配置systemd等进程管理器。本轮没有停止或重启已有模型服务。模板随项目位于deployment/models/qwen3_reranker.jinja，不需要个人绝对路径。

默认Embedding端口8001、maxlen8192、GPU预算0.35；Rerank端口8002、maxlen4096、预算0.35；均pooling runner。Rerank的HF overrides保留Qwen3ForSequenceClassification、no/yes分类token与is_original_qwen3_reranker。协议依据[vLLM评分文档](https://docs.vllm.ai/en/stable/models/pooling_models/scoring/)，不是Chat接口。

默认绑定127.0.0.1；WSL NAT下Windows不能访问时先检查Windows的WSL localhost转发。确需MODEL_HOST=0.0.0.0时必须用防火墙限制访问，并把后端URL改为实际可达地址；不要未经鉴权公开服务。

PowerShell，后端目录验证：

```powershell
cd 'Cultural heritage ai'
.\.venv\Scripts\python.exe -u -m scripts.test_vllm_embedding
.\.venv\Scripts\python.exe -u -m scripts.test_vllm_rerank
cd ..
```

预期query/document dimension均1024、Embedding smoke PASSED、Rerank天坛排首位。GET /v1/models应提供对应served alias。

## 7. 初始化Milvus和公开知识

根目录，两个模型及Milvus已启动：

```powershell
& '.\Cultural heritage ai\.venv\Scripts\python.exe' deployment/init_milvus.py --collection heritage_chunks --apply --index-demo
& '.\Cultural heritage ai\.venv\Scripts\python.exe' deployment/init_milvus.py --collection heritage_chunks
```

与后端RAG_MILVUS_COLLECTION_NAME保持一致。预期dimension1024、dense HNSW、sparse BM25、verification_hits大于0。缺失集合只在--apply时新建；已有不匹配集合报错，不能自动删库重建。

最小资料仅为公开天坛/故宫/长城说明，不含真实用户内容。可选评测语料重建（后端目录，明确写入当前配置集合）：

```powershell
cd 'Cultural heritage ai'
.\.venv\Scripts\python.exe -u -m scripts.index_rag_eval_documents
.\.venv\Scripts\python.exe -u -m evaluation.runners.rag_retrieval_runner --strategy hybrid_rerank --report-dir ../.verification/retrieval
cd ..
```

保留既有评测输出，不覆盖历史结果。完整评测标签与最小语料不等价，勿把少量演示命中率当完整指标。

## 8. 启动API和前端

终端C，后端目录：

```powershell
cd 'Cultural heritage ai'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

终端D，前端目录：

```powershell
cd 'culture-front/Cultural heritage'
npm run dev
```

或依赖安装后根目录npm run dev同时启动；它不替代基础服务和模型。浏览器5173、Swagger8000/docs。根目录npm run check:running验证API、Vue和代理存活。

```powershell
& '.\Cultural heritage ai\.venv\Scripts\python.exe' deployment/health_check.py --inference
& '.\Cultural heritage ai\.venv\Scripts\python.exe' deployment/smoke_test.py --create-demo
```

默认健康检查拒绝非回环目标，确认配置正确后可显式--allow-remote。检查不打印密码或响应正文。smoke创建两个新账户和一个2人任务，仅交1份，不触发聚合。应显示真实MySQL/Redis/HTTP成功。完整评委步骤见JUDGE_GUIDE.md。

npm run build后可npm run preview在4173本地预览；实际服务器发布dist须配置SPA回退及/api反代。当前不提供已验收的公网生产拓扑。

## 9. 配置LLM、HCP与众包消费者

后端.env填写DEEPSEEK_API_KEY。DEEPSEEK_BASE_URL是Settings字段，但运行请求地址取数据库Agent.api_url，不能只改这个环境变量期望覆盖已注册Agent。LLM模型名也在Agent记录，不存在虚构的统一LLM_MODEL环境变量。

根目录，先明确所选服务支持的模型ID；以下注册不发送Key，也不自动执行模型调用：

```powershell
$llmModel = Read-Host '输入服务商实际支持的模型ID'
& '.\Cultural heritage ai\.venv\Scripts\python.exe' deployment/register_agents.py --llm-url https://api.deepseek.com/chat/completions --llm-model $llmModel
# 核对dry-run后实际注册；可复用同配置同名记录，不覆盖不同记录。
& '.\Cultural heritage ai\.venv\Scripts\python.exe' deployment/register_agents.py --llm-url https://api.deepseek.com/chat/completions --llm-model $llmModel --apply
```

本地无Key的LLM可使用--auth-type none和自己的完整chat/completions地址。可为不同Agent手动注册不同模型，脚本三个配置使用同一底层模型不构成多模型多样性。

前端Agent页逐个健康检查，或注册命令加--check-health（真实请求，可能计费）。两worker与judge需enabled/available。把返回competition-judge ID填入RAG_TASK_AGGREGATOR_AI_ID，设置RAG_TASK_CONSUMER_ENABLED=true并重启API。首次部署先保持false，确认队列、权限和模型后再启用。然后按评委指南创建HCP任务及达标众包任务。最终源码允许不填写聚合ID，由公共Agent排名选择启用、URL有效且available或尚未检查的Judge并持久绑定；显式ID为可选覆盖。消费者仍需开启。当前完整付费链路未验证。

## 10. 排错与安全边界

| 现象 | 检查 |
|---|---|
| /health正常但功能失败 | 它仅是存活；运行health_check、database check并看本地日志 |
| Redis失败却可登录 | 开发内存降级；直接PING应成功才算Redis验收 |
| 模型404/别名错误 | /v1/models别名与.env一致；新Rerank默认已改qwen3-reranker |
| GPU OOM | 关闭无关GPU进程、降低输入/并发或分开运行；保留原模型和接口 |
| Milvus BM25/schema错误 | 检查2.6服务与字段/索引；另建新集合验证，勿drop原集合 |
| collecting/aggregating不前进 | 阈值、聚合ID、consumer开关、job租约与last_error；不能伪造完成 |
| 前端401/CORS | 重新登录，核对API URL、精确origin、代理及JWT/Redis状态 |
| MySQL初始化拒绝 | 已有表时只check；备份审查后人工迁移，不自动清理 |
| 三项SQL测试失败 | 历史迁移文件缺失，完整新库SQL不替代其历史摘要契约 |
| 打包拒绝原历史 | 使用--sanitize-history，先轮换历史已暴露凭据，保留原仓库 |

/ais与/admin/rag/documents未做管理员隔离，任意Agent URL有SSRF风险；当前只用于受控本地演示。不要公开模型端口、数据库端口或管理接口。生产部署仍需权限、TLS、网络策略及依赖漏洞修复。
