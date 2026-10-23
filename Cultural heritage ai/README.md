# Cultural Heritage AI 后端

Python3.11 / FastAPI / MySQL / Redis / Milvus。先阅读根目录[部署教程](../docs/DEPLOYMENT.md)、[配置说明](../docs/ENVIRONMENT.md)、[设计](../docs/SYSTEM_DESIGN.md)和[审查报告](../docs/AUDIT_REPORT.md)。

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
# 配置并初始化基础服务后启动。
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
.\.venv\Scripts\python.exe -m pytest tests -q
```

应用不会自动建表。当前项目的正式新库结构入口是 [sql/mysql_schema.sql](sql/mysql_schema.sql)，仅包含 17 张有效业务表，只供人工审查后在空库初始化。`sql/createtable.sql` 和 `../deployment/sql/user_export_reference.sql` 是原始 19 表导出，保留用于历史比较，不应用于新部署。父目录 `../deployment/sql/mysql_schema.sql` 仍是清理前的 19 表版本，本轮没有修改工作区外的部署文件，也不应直接用于当前新部署。若使用父目录部署工具，必须先仅执行 `render` 重新生成并审查结构；不得在现有库执行 `init`。`migrations/` 是历史增量记录，不应在完整新库重复执行。两张旧物理表由用户备份、检查依赖后手动删除，见 [清理记录](docs/LEGACY_TABLE_CLEANUP.md)。

`/health` 仅表示进程存活。本轮旧表清理后完整测试为 **1187 passed / 3 failed**；3 项失败仍来自缺少两份历史增量 SQL，没有删除或放宽测试。验证命令、全部修改和人工删表前检查见 [清理记录](docs/LEGACY_TABLE_CLEANUP.md)。历史设计文档保留。
