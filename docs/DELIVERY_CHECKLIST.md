# 交付完整性清单

审查日期2026-10-23；这是带已知限制的本地原型交付，不是“所有测试通过”的声明。

## 功能与真实服务

- [x] 前端现有路由、API、状态展示与后端契约审查。
- [x] 后端全量测试已执行：1160通过，3项缺迁移文件失败；未跳过或改摘要。
- [x] 后端ruff、前端类型/ESLint/Oxlint/构建通过。
- [x] Node契约与双用户浏览器测试通过；浏览器数据库为临时SQLite。
- [x] 独立真实MySQL+Redis+HTTP/JWT最小众包流程通过。
- [x] 原配置MySQL、Milvus、Embedding、Rerank可连接；Embedding查询/文档1024。
- [x] 真实Rerank天坛排首约0.9949；不是Mock或Chat替代。
- [x] 真实Milvus新集合从零建schema、HNSW/BM25、公开资料入库、Hybrid+Rerank检索通过。
- [x] ORM完整19表新空库初始化无差异。
- [x] 用户补充19表SQL独立空库导入通过；缺answers.execution_id外键，唯一索引差异记录。
- [x] 新Compose配置静态校验、两启动脚本bash语法通过。
- [ ] 原配置Redis连接超时，需修正网络/地址/凭据；独立Redis验证成功不代表原Redis恢复。
- [ ] 真实外部LLM、完整HCP执行、众包聚合→入库→前端最终结果展示未验收，聚合ID未显式覆盖且consumer关闭；最终代码可自动选Judge，但尚未做真实付费聚合验收。
- [ ] 新Compose五服务组合在全新主机整体启动尚未验证。
- [ ] GPU长上下文双服务并发、吞吐、崩溃与网络故障压力未验证。
- [ ] mypy尚有6项既有错误。
- [ ] npm audit仍19项（1critical/15high/2moderate/1low），未强制升级。
- [ ] 管理员权限/SSRF/公网安全闭环未完成，不应开放公网。

## 依赖与手动配置

- [x] Python requirements与开发检查版本、前端package-lock提供。
- [x] 两模型固定来源/版本、下载与独立启动脚本、Rerank模板与许可证提供。
- [ ] 新机器需操作者自行下载权重；本轮没有自动下载。
- [ ] 已测试vLLM配置需要兼容NVIDIA GPU/驱动、WSL或Linux和独立Conda环境。
- [ ] 完整问答/聚合需可用LLM API及可能付费的Key；两个worker与judge需注册/健康检查。
- [ ] consumer需开启并重启；未指定聚合ID时自动选择符合条件的Judge，演示建议显式指定。
- [ ] 结构化heritage_sites示例行未自动写入，需授权资料时人工导入。
- [x] 最小公开RAG资料随包，不含真实账户、提交或数据库导出行。

## 打包与复现

- [x] 交付工具16项测试通过：秘密/历史拒绝、脱敏、路径穿越、篡改、确定性、schema稳定。
- [x] 原Git历史秘密风险发现并阻止原始bundle，不改源Git。
- [x] 提供临时仓库脱敏历史选项，明确哈希变化与排除范围，不虚构原始历史。
- [x] ZIP秘密扫描、内部Git对象重新扫描、SHA清单、CRC与解压结构验证通过；最终校验结果随release/*.verification.json提供。
- [x] ZIP独立解压、全新Python虚拟环境安装及pip check通过；全量1160通过/3既有失败。
- [x] 解压版npm ci --ignore-scripts、类型/静态检查/构建、31项Node契约、Chrome双用户验收通过。
- [x] 工具测试覆盖解压后无.git复用已扫描历史，并重建相同SHA的ZIP；最终包另作实际重包比对。
- [x] Windows Python3.11完整依赖锁已记录；未把其他操作系统安装视为已验证。
- [ ] 全新物理机/全新GPU驱动/权重重新下载未验证，不能声称完整无条件复现。

根目录打包：
```powershell
& '.\Cultural heritage ai\.venv\Scripts\python.exe' scripts/build_release.py --sanitize-history
& '.\Cultural heritage ai\.venv\Scripts\python.exe' scripts/build_release.py --verify-only release/culture-ai-competition.zip
```

源代码是当前工作区；SOURCE_HISTORY.json记录单独脱敏历史。ZIP排除.env、私密值、node_modules、虚拟环境、缓存、日志、模型权重、真实数据与无关历史报告。

## 提交前人工补充

- [ ] 轮换历史记录中曾提交的凭据，检查曾共享副本；不要在聊天粘贴新Key。
- [ ] 确认赛事是否接受脱敏历史，或提供评委可访问的合规仓库；现远程匿名返回404。
- [ ] 处理测试/类型/依赖安全遗留问题后重新验收，不以本报告替代。
- [ ] 填写参赛登记表、作者信息、参赛声明、版权及第三方授权材料。
- [ ] 制作符合赛事规格的真实演示视频和截图，展示实际功能与限制。
- [ ] 现场配置服务密码/JWT/LLM Key、GPU/模型、Agent和consumer，完成评委指南完整付费流程。
