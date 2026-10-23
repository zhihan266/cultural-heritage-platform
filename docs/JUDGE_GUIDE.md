# 评委测试指南

先按DEPLOYMENT.md启动基础服务与前后端，使用新演示数据库。当前测试结论见DELIVERY_CHECKLIST.md。

## 不使用付费模型的最小流程

1. 浏览http://127.0.0.1:5173，两个独立浏览器上下文注册A、B。
2. A发布“整理天坛用途、事实来源与不确定点”，人数2。
3. B从大厅发现、接取、提交；刷新后仍显示本人原答案与已提交状态。
4. B“我的接取”有任务，“我的发布”没有A的任务。A查看进度1/2。
5. 此时collecting为正确状态，不应有伪造正式知识，不触发聚合付费调用。
6. 退出、重新登录后恢复任务；未登录访问私有列表返回401。

根目录可运行等效HTTP验证（创建两个新账户和一条任务，保留审计记录）：

```powershell
& '.\Cultural heritage ai\.venv\Scripts\python.exe' deployment/smoke_test.py --create-demo
```

不加--create-demo只读健康和OpenAPI。npm run test:crowd-http使用临时SQLite及独立后端，不等于MySQL/模型全链路。

## 本地模型和知识

```powershell
& '.\Cultural heritage ai\.venv\Scripts\python.exe' deployment/health_check.py --inference
& '.\Cultural heritage ai\.venv\Scripts\python.exe' deployment/init_milvus.py --collection heritage_chunks
```

预期查询和文档向量1024维，天坛相关性优于故宫，直接Redis PING成功。部署教程的--index-demo会写入公开资料并真实检索；稳定文档ID用于重复入库，不删除其他知识。

## 完整HCP和众包演示（LLM可能计费）

按教程注册两个worker和一个judge并手动健康检查available。系统问答提交天坛与故宫用途比较，观察waiting/answering/aggregating至completed或明确失败，检查正式结果和公开执行记录。两个Agent可用同一模型，但不代表模型多样性。

设置聚合Agent并启用消费者，再由第三账户提交第二份众包回答达到2/2。观察aggregating→ingesting→completed，检查正式知识、文档ID、片段数和入库回执；随后检索该知识。不确定调用不能当成功，重复提交不得创建重复正式结果。

本轮未执行付费LLM全流程；不以单元测试、SQLite浏览器或独立公开资料入库替代这部分证据。

## 异常和参赛材料

可验证空问题、重复接取/提交、其他账户私有数据访问；预期校验或权限错误且无堆栈泄露。不要删除集合、清空数据库或停止他人服务来演示。

登记表、参赛声明、作者/版权证明、第三方授权清单、正式演示视频、赛事格式截图与仓库权限由作者补齐。视频展示真实执行及未验证边界，不使用Mock最终结果。
