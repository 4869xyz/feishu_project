# logs 目录

机器人运行日志默认写入 `logs/feishu_bot_listener.log`；进程单实例锁位于 `logs/feishu_bot_listener.lock`。

- `.log` 和 `.lock` 都属于运行产物，已通过 `.gitignore` 排除，禁止提交。锁文件可以在进程退出后保留，是否仍被进程持有才代表实例是否运行。
- 日志可用于定位网络、权限和附件下载问题，但不得包含 App Secret、完整 token 或附件二进制内容。
- Lark SDK 使用项目根日志管线且只保留 `WARNING` 及以上记录；`httpx` 的 URL 型 INFO 日志也被抑制，以避免重复输出和用户标识泄露。
- 日志格式化器会把 URL 查询参数中的 `access_key`、`ticket`、`access_token` 和 `app_secret` 值替换为 `***`，但新增日志仍必须避免主动记录凭据或业务数据。
- `.gitkeep` 用于保留空目录结构。
