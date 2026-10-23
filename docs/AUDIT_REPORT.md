# 参赛交付审查报告

日期2026-10-23。审查范围为当前前后端源码、路由、组件、配置、模型、服务、SQL、迁移、测试和文档。程序清点与读取约465个后端文本文件/55864行、55个前端文本文件/11181行，并重点核对鉴权、执行、查询和持久恢复。代码存在大量任务开始前的未提交改动，本轮保留，不能把git diff全部归为本轮修改。

## 总体结论

具备可演示的前后端、真实本地Embedding/Rerank和Hybrid知识索引；完整空库初始化已验证。可以交付“受控本地原型”，不能宣称公网生产就绪、所有测试通过或付费模型端到端已验收。原始Git历史和当前配置不得直接打包。

## 问题、级别与处理

| 级别 | 发现 | 处理和边界 |
|---|---|---|
| P0交付阻塞 | 原始Git历史含.env.production及与当前配置一致的私密值 | 默认打包拒绝；临时仓库脱敏导出，源Git不改；作者需轮换相关凭据并审查既有分享范围 |
| P1安全 | /ais注册/列表/检查与/admin/rag/documents无管理员鉴权；任意Agent URL有SSRF风险 | 新部署仅回环，文档明确受控演示；未冒然重写权限体系，公网发布仍阻塞 |
| P1安全 | npm audit共19项：1 critical、15 high、2 moderate、1 low | 保留结果，未运行audit fix --force；生产与公开部署需单独升级验收 |
| P1部署（已补齐） | 初次审查的sql/createsql.sql无建表；用户随后补充完整19表，用户已同步完整导出（最终工作区名createtable.sql） | 保留完整原文；另增可移植ORM完整SQL及拒绝非空库初始化器 |
| P1验证 | 三项既有测试依赖两份缺失历史SQL | 原样保留失败，不生成假历史摘要，不skip测试 |
| P1环境 | 原配置Redis真实PING超时 | 报告失败；另用独立Redis7.4.2+新MySQL库完成真实业务验证，不以开发内存降级冒充成功 |
| P1未验收 | 聚合ID未覆盖、consumer关闭；最终源码支持未绑定任务自动选Judge | 未调用付费外部LLM，真实HCP/众包聚合至入库和UI最终态未验收 |
| P2部署 | Rerank代码/示例原默认为仓库模型ID，运行服务使用qwen3-reranker | 改为真实served alias；不修改Rerank算法或协议 |
| P2一致性 | 用户完整导出与现有库answers.execution_id缺外键 | 独立可选补丁，先查孤儿和现有约束，ALTER仅注释供审阅；未执行现有库DDL |
| P2类型 | mypy在4文件有6个既有错误 | 保留，详见下节，不以调整检查范围掩盖 |
| P2依赖 | 浏览器测试使用Playwright但未在前端依赖中声明 | 加入已实际运行的1.62.1并更新lock；无需下载浏览器 |
| P2体验 | HCP历史主要依赖浏览器已知ID；没有跨设备完整列表 | 据实说明，未扩展业务范围 |
| P2约束 | accounts.username只有普通索引，注册查重主要在应用层 | 并发注册同名的数据库级唯一性需后续独立设计；本轮不改既有账户数据 |

npm风险涉及运行依赖axios/vue及构建工具vite等，critical来自shell-quote传递依赖。审计是依赖级报告，不等于所有漏洞都能从当前页面利用；同样不能因构建通过忽略。Vite devtools传递依赖存在peer版本告警，构建仍成功。Python未另做完整CVE扫描。

## SQL核对与用户补充

初次检查的仓库sql/createsql.sql没有CREATE TABLE。用户随后提供完整HeidiSQL导出，并在工作区使用sql/createtable.sql命名；本轮原样保存deployment/sql/user_export_reference.sql，不再把“原仓库文件不完整”误述为“用户提供SQL缺全部表”。

用户导出包含19表，无INSERT/REPLACE用户行、无Key。固定CREATE DATABASE/USE aijiu_dev，不能直接当通用新库初始化。已在独立heritage_export_verify_20261023空库验证；仅验证副本重定向数据库，原文不改。

逐表核对列、类型、长度、NULL、声明的server default、主键、唯一约束、外键和索引：
- 无缺表、缺列、类型、长度、NULL、主键差异。
- answers.execution_id缺至agent_executions.id外键；该列已有普通索引。
- heritage_sites.name为UNIQUE uk_heritage_sites_name，ORM为普通ix_heritage_sites_name。名称不同且导出约束更强，现有唯一索引满足查询，不应为名字重复加索引。
- 导出部分updated_at有ON UPDATE CURRENT_TIMESTAMP，ORM新库DDL主要由应用更新；属于额外自动更新时间语义。检查器比较默认初值，不将ON UPDATE误认为初值冲突，也未自动消除此差异。
- 当前ORM与导出都没有真实初始账户/Agent数据；注册流程和公开资料代替生产数据导出。heritage_sites可为空，结构化查询工具会缺少实际资料，需操作者导入授权资料。
- 两份旧测试引用的20261022_add_rag_task_tables.sql、20261022_add_rag_task_crowd_workflow.sql在工作区缺失；新完整SQL不能满足历史文件摘要测试。

新mysql_schema.sql按ORM依赖顺序生成，含19表及全部声明约束/索引；在独立heritage_verify_20261023空库导入并检查无差异。本轮未覆盖原SQL、现有库或历史数据；用户补充SQL期间，工作区SQL已更新为同一完整导出并更名sql/createtable.sql，SHA256与保留的附件原文一致。MySQL版本8.0.41。

## 实际验证记录

| 层级 | 结果 |
|---|---|
| 后端全量pytest | 1160通过、3失败，221.24秒；均缺历史SQL文件 |
| 后端ruff | 通过 |
| 后端mypy app evaluation scripts | 6错误/4文件，286源码文件 |
| 前端Node契约测试 | 31通过；干净解压安装后复验同样通过 |
| 前端类型、ESLint、Oxlint、构建 | 通过；大chunk和第三方PURE注释告警 |
| Playwright双用户 | Chrome真实Vue→Vite→HTTP→JWT→临时SQLite通过 |
| 真实MySQL/Redis/API | 独立新库+Redis7.4.2临时容器+独立API端口；两账户注册登录、发布/发现/接取/回答与个人列表通过 |
| 真实Embedding | query与document均1024，smoke PASSED |
| 真实Rerank | 天坛排首，分数约0.9949，高于故宫/长城 |
| 真实Milvus | 新competition_verify_20261023，HNSW+BM25建立，公开资料入库，经Hybrid+Rerank命中1条 |
| 原配置健康 | MySQL、Milvus、两模型通过；Redis TimeoutError |
| Compose/模型脚本 | config --quiet及bash -n通过；新Compose五服务尚未整体拉起验收 |
| 真实检索评测 | 默认Dense，当前80条标签全部执行完成；新报告写入独立验证目录，未覆盖历史结果 |
| 交付工具pytest | 16通过，覆盖秘密、脱敏历史、ZIP路径/篡改、确定性、DDL稳定性 |
| 干净解压复现 | 独立Python环境安装及pip check通过；全量1160通过/3历史SQL失败（316.92秒）；npm ci --ignore-scripts、类型/静态/构建、31项契约和Chrome双用户流程通过 |

三项pytest失败：
- tests/rag_task/test_crowd_schema.py::test_new_sql_matches_all_four_orm_tables_and_existing_answer_alter
- tests/rag_task/test_crowd_schema.py::test_published_stage_one_migration_is_unchanged
- tests/test_execution_architecture.py::test_incremental_sql_matches_all_new_orm_columns_indexes_constraints_and_defaults

mypy错误位置：app/modules/rag/domain/vector_store.py:18缺少返回；scripts/build_rag_hard_eval.py:457缺变量注解；evaluation/services/answer_variant_executor.py:227、252、260的usage类型；evaluation/runners/rag_retrieval_runner.py:607检索器赋值类型。

## 本轮实际修改

- app/core/config.py：仅修正Rerank默认served alias为qwen3-reranker。
- 后端.env.example：新部署库/用户占位、随机JWT提示、Redis密码、CORS、关闭启动模型探测、清除Key假文本和已失效迁移说明；保留全部53个真实Settings键。
- requirements-lock-windows-py311.txt：记录独立解压安装并测试过的完整Windows Python3.11依赖版本。
- 三处README：统一到本轮部署、测试、交付入口和真实限制。
- 前端package.json/package-lock.json：补锁Playwright1.62.1，保持现有页面与业务。
- deployment/common.py、database.py：从正确目录加载配置，确定性ORM DDL、新空库初始化、只读比对。
- deployment/sql/：完整新库SQL、用户导出原文、可选外键补丁。工作区新增sql/insectdata.sql仅包含Agent INSERT，作为运行配置数据排除交付，不自动导入、不删除原文件。
- deployment/compose.yml、.env.example：独立MySQL/Redis/Milvus依赖和回环发布，新卷初始化与健康检查。
- deployment/models/：固定仓库SHA、显式下载、两服务独立启动、原模板、Apache许可证与来源说明。
- deployment/init_milvus.py：复用现有适配器，新集合创建、索引校验、显式公开演示入库检索。
- deployment/health_check.py、smoke_test.py：直接依赖探测、真实本地模型请求和明确写入的最小HTTP业务测试。
- deployment/register_agents.py：默认dry-run，显式注册两worker与judge，复用同配置同名记录，不写Key。
- scripts/build_release.py：白名单收集、私密值和模式扫描、历史拒绝/临时脱敏导出、固定ZIP属性、SHA清单、CRC和解压检查。
- tests/test_release.py：交付安全及可重复验证。
- docs/：项目、设计、部署、配置、评委指南、清单、本报告与VERIFICATION.json结构化验证摘要。

本轮未修改HCP评分/辩论策略、Embedding业务逻辑、Milvus检索算法、Rerank逻辑、已有评测结果，未清空任何数据库/集合。最终打包前检测到工作区同期新增的众包恢复改动：main.py消费者日志，aggregation_agent.py自动排名选择Judge，crowd_processing.py未绑定job原子绑定，crowd_service.py未领取聚合恢复与失败重试分离，facade.py重试选择，以及processing_repository.py绑定执行记录。这些非本轮部署工具编写的改动已保留并纳入最终快照复验，不能将此前旧快照测试视为它们通过的证据。验证新建两MySQL库和一Milvus集合供审计；临时Redis和独立API由本轮进程退出后停止，未停止原服务。

## Git与比赛材料

原远程地址匿名API访问返回404，不能保证评委访问；不擅自设为public或推送。默认原始bundle被秘密扫描拦截。交付使用--sanitize-history：临时仓库保留HEAD祖先提交的作者、日期、消息和拓扑，排除配置/运行文件/不安全内容；提交附脱敏说明和原哈希，新哈希明确披露，原仓库不变。前端尚无已提交历史，不虚构提交。

若赛事强制未改写的原始历史或公开仓库，需作者确认比赛规则、轮换凭据并提供合规仓库；脱敏导出不是原始历史证明。登记表、参赛/版权声明、正式演示视频尚未提供，列为人工材料。
