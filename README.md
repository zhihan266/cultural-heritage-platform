# 文化遗产 AI：多智能体问答与众包知识生产

Vue 3 前端与 FastAPI 后端组成的文化遗产问答原型，集成 HCP-MAD、Hybrid RAG 和众包知识整理，保存任务、回答、执行记录与入库回执。

**交付定位：受控本地比赛演示。** 当前没有管理员角色隔离，不能直接作为公网生产服务开放。真实付费模型聚合未验收，详见[审查报告](docs/AUDIT_REPORT.md)。

## 目录与技术栈

| 目录 | 内容 |
|---|---|
| Cultural heritage ai/ | Python3.11、FastAPI、SQLAlchemy、MySQL/Redis、业务测试与评测 |
| culture-front/Cultural heritage/ | Vue3、TypeScript、Pinia、Element Plus、Axios、Vite8 |
| deployment/ | MySQL/Redis/Milvus Compose、初始化、健康检查、演示资料 |
| deployment/models/ | 模型固定来源下载、两个 vLLM 启动脚本、模板与许可证 |
| docs/ | 本轮交付文档 |
| scripts/build_release.py | 安全扫描、历史脱敏、可重复 ZIP、清单与解压校验 |
| tests/ | 交付工具测试；后端业务测试在后端 tests/ |

## 快速开始

Windows PowerShell，从解压后的根目录执行。基础服务、GPU、模型、API Key 的完整步骤和成功判定见[部署教程](docs/DEPLOYMENT.md)。

```powershell
cd 'Cultural heritage ai'
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
cd ..
Copy-Item deployment/.env.example deployment/.env
# 按环境说明填写本地随机密码、JWT 和地址，再启动基础服务。
docker compose --env-file deployment/.env -f deployment/compose.yml up -d
# 在 WSL 两个终端启动模型后初始化公开知识。
& '.\Cultural heritage ai\.venv\Scripts\python.exe' deployment/init_milvus.py --collection heritage_chunks --apply --index-demo
cd 'culture-front/Cultural heritage'
npm ci
cd ../..
npm run dev
```

首次 Compose 新数据卷自动导入完整 19 表 SQL。已有服务先检查端口和数据卷，勿覆盖。非 Compose 空库初始化及 Agent 注册见部署教程。未配置 LLM 时只能演示登录与众包收集，不能宣称问答和聚合完成。

前端 http://127.0.0.1:5173 ，API 文档 http://127.0.0.1:8000/docs 。/health 仅表示进程存活。

## 测试和打包

```powershell
cd 'Cultural heritage ai'
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy app evaluation scripts
cd ..
& '.\Cultural heritage ai\.venv\Scripts\python.exe' -m pytest tests -q
cd 'culture-front/Cultural heritage'
npm test
npm run type-check
npx eslint .
npx oxlint .
npm run build
cd ../..
& '.\Cultural heritage ai\.venv\Scripts\python.exe' scripts/build_release.py --sanitize-history
```

输出 release/culture-ai-competition.zip、SHA256 和验证 JSON。原始历史含私密配置，默认打包会拒绝；脱敏导出只修改临时仓库，SOURCE_HISTORY.json 说明范围与哈希变化。赛事若强制原始哈希，需作者按规则处理，不能把脱敏历史当原始历史。

后端全量1160通过、3项缺少迁移文件失败，mypy有6项既有错误；前端检查与构建、本地模型实际测试通过。npm audit报告19项风险，完整结论见交付清单。

## 文档入口

- [项目说明](docs/PROJECT_DESCRIPTION.md)
- [系统设计](docs/SYSTEM_DESIGN.md)
- [完整部署](docs/DEPLOYMENT.md)
- [环境变量](docs/ENVIRONMENT.md)
- [评委指南](docs/JUDGE_GUIDE.md)
- [交付清单](docs/DELIVERY_CHECKLIST.md)
- [审查报告和修改清单](docs/AUDIT_REPORT.md)
