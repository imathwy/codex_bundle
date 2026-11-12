# Codex bundle 0.167.2 升级与登录修复

日期：2026-11-12。目标版本来自官方稳定发布 `rust-v0.167.2`，上游提交
`ce21072c90d32c40af7a55b60b2fb35c84a43a6a`。
[官方发布说明](https://learn.chatgpt.com/docs/changelog)。

## 已复现的重复登录问题

`zcwang-gpt-2` 的当前窗口已登录，但登录文件只存在于隔离的运行目录。
对应 profile 的 trusted state 与兼容目录均没有 `auth.json`，仍保留
`auth.json.missing`。旧启动器在首次登录时不启动 watcher，并且只在正常退出后
保存凭据。因此登录窗口保持打开或被终止时，新开的 resume 可能要求重新登录。

本次从经 `/proc` 核对 HOME/CODEX_HOME 的当前活跃进程恢复了该 profile 的登录态。
未打印 token，未复制其他 profile 的凭据，也未改动默认选中的 profile。
修复后，独立 `login status` 返回 `Logged in using ChatGPT`，实际 `resume
zcwang-gpt-2` 直接显示历史会话列表。

## 启动器修复

- 新增 `bin/codex-auth`：首次登录和同账号刷新按现有 guard 间隔实时保存；退出及
  可处理的终止信号再次同步。通常在 0.5 秒内可供后续窗口使用。
- 凭据比较与写入在同一个 profile 锁内进行；同时遵守运行时的 `auth.lock`。
  旧窗口不能覆盖新登录、回滚其他窗口已保存的刷新或复活已登出的凭据。
- 空白兄弟窗口可接收该 profile 的首次登录；已绑定账号的窗口不能隐式换账号。
  无效 JSON、空凭据、未授权账号切换和凭据符号链接均不写入 trusted state。
- file credential store 保证账号不依赖随机运行目录的 keyring 项。
- guard 使用最新 trusted state，避免恢复旧账号快照；清理 trap 保留运行目录的
  同步和删除逻辑。未删除历史遗留目录或停止用户已有窗口。
- 正确识别全局参数之后的 `login`/`logout`，并清除继承的 `CODEX_TOOL_CALL_ID`。
- 空数据库尚未有 `threads` 表时，启动前的路径修复直接跳过，避免新 profile
  首次启动报 `no such table: threads`。
- `source bin/env` 正确定位 Bash/Zsh 中的脚本自身路径。

## 升级与兼容性

- Codex 从 0.164.0 升级到 0.167.2，保留共享会话、稳定写入租约、活跃写入者 fork、
  provider 回退、配置保护、SQLite 并发启动与过载重试补丁。
- 人工移植上游重构涉及的会话筛选、启动清理、错误分类和 SQLite 连接模块；
  新版发布 rollout 的路径也检查持久租约。完整源码差异随运行时保存。
- 修复 updater 的构建目标列表，补上新版 `codex-exec-server-cli` 包及
  `codex-exec-server` 二进制，否则原升级器会报 `no bin target named`。
- uv/uvx 从 0.12.13 升级到 0.13.7；使用 PyPI 官方发布包并校验 SHA-256：
  `2c9bdc4a872b60a93c5f26c0791089ea11585a4f9d3b3c017d78eb8487505e25`。
- 保留 Git 2.55.0 和对应源码归档。未更改模型选择或 profile 的用户配置。
- 新版本只有一项新增共享状态迁移：为 threads 添加 attached parent 列与索引。
  shared state、goals、memories 已通过 SQLite 在线备份 API 创建一致性副本并通过
  quick_check；没有直接复制活跃 WAL 数据库。

## 验证与回退

已完成验证：

- Bundle 回归测试：29 项通过，包括登录窗口尚未退出时再次 resume、SIGTERM 后
  再次启动、旧窗口与新登录/登出的竞争、账号隔离和配置保护。
- 新运行时会话锁测试：4 项通过，覆盖持久租约、旧锁文件被删除、发布操作与活跃
  写入者竞争。目录枚举断言已排序，不依赖文件系统返回顺序。
- 新运行时错误协议测试：33 项通过。
- 28 个 profile 的 config/auth 语法检查通过；`codex-profile doctor` 通过。
- 切换后的 `codex --version` 为 0.167.2；`zcwang-gpt-2` 的 `login status` 显示
  ChatGPT 已登录。真实 TTY 中打开 resume，成功显示已有会话列表，未要求登录；
  随后通过 Ctrl-C 正常退出，未选择或接管已有会话。
- 独立 `codex-exec-server --help`、uv/uvx 版本检查通过。
- Bundle、运行时文件及归档分片的 SHA-256 校验全部通过；脚本语法及 diff 空白检查通过。

未运行完整上游测试集，也未发起真实模型生成请求。当前容器的默认 bwrap 沙箱存在
宿主权限限制，相关 shell 验证使用环境提供的审批执行；没有修改系统权限或沙箱策略。

构建、测试及升级日志位于 `.work/audit-20261112/`。
脚本、uv/uvx 和共享数据库升级前副本位于 `backups/audit-20261112/`。
旧 0.164.0 运行时和分片归档保留。需要回退运行时可执行：

```bash
./bin/update-codex-runtime --rollback 0.164.0
```

升级或回退后重新打开 Codex 窗口；已运行进程仍使用启动时加载的旧代码。
数据库快照用于人工恢复，不应在活跃会话仍运行时覆盖回去，否则会丢失备份后的更新。
源码、下载、构建、缓存、日志和临时文件全部保存在持久化工作区。未提交或推送 Git。
