# 项目与示例资料包

包含当前源代码、前端构建产物、现有会话/报告/搜索证据/任务检查点数据库，以及 examples/reports.json 报告导出。
数据库采用 SQLite 在线一致性备份，包含备份时已提交的数据；未修改原项目数据库。失败和部分完成的历史记录也原样保留。

## Windows 启动
在解压后的 be-seen 目录中运行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
npm.cmd --prefix frontend ci
Copy-Item .env.example .env
powershell -ExecutionPolicy Bypass -File scripts\Start-Local.ps1
```

访问 http://127.0.0.1:5173 ，示例口令见 .env.example。已有报告可直接查看；新搜索及对话需在 .env 填写元宝搜索和兼容模型服务配置。真实密钥没有打包。
数据库已经放在 backend/data/beseen.db，不需要再次导入。请勿用空数据库覆盖它。
依赖需要首次安装；完整操作说明见 README.md。

## Docker 携带示例启动
复制 .env.example 为 .env 并配置后，执行：

```sh
docker compose -f compose.yaml -f compose.examples.yaml up --build -d
```

此命令将打包的 backend/data 绑定到 API 与 Worker，访问 http://127.0.0.1:8080 。只使用默认 compose.yaml 会创建独立空卷，而不会载入本包示例。

示例清单和备份时任务状态见 examples/manifest.json。若备份时存在排队/运行任务，启动 Worker 后会恢复；只想浏览历史时先仅启动 API 与前端。
