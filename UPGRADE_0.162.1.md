# Codex bundle 升级至 0.162.1

活动运行时从 0.156.1 升级到官方 `rust-v0.162.1`，提交
`092d3acd6bec3e3a14bdc7e7a2810ab628ab759d`。从 0.156.1 移植本地补丁，保留
账号配置保护、持久写入租约、活跃写入者自动 fork、共享会话及跨 provider 恢复。

上游协议重试、SQLite 恢复、TUI 启动及异步 provider 查询接口发生变化；六处
冲突已逐项适配。保留新版 Flex/内容过滤处理、数据库损坏恢复和启动选项，并
继续在既有 WAL 数据库及已完成的迁移集上避免不必要的写锁。

## 工作区存储规则

- 仓库 `AGENTS.md` 在每次启动时合并到私有运行时全局指令，同时保留 profile
  原有全局指令。生成的合并文件不回写 profile。
- 临时运行 home、保护快照、handoff、历史索引器与升级器工作目录默认位于
  bundle 的 `.work/`；运行时缓存默认也位于该目录。`.work/` 已加入 Git 忽略。
- 子进程的 `TMPDIR`、`TMP`、`TEMP` 指向 profile 的 `.cache/tmp`。升级构建的
  Rust、Python、开发依赖与下载缓存全部位于工作区内，未安装到系统目录。
- `CODEX_RUNTIME_HOME_ROOT` 可覆盖运行目录，并已加入安全环境变量白名单。
  旧 `/tmp` 辅助链接及 `/var/tmp` rollout 引用保留读取兼容，不在旧位置创建
  新数据，也不删除原有容器内数据。

## 构建与验证

使用工作区内 Rust 1.95.0、Python 3.12.14 和 libcap 开发文件，通过上游包
构建器构建 CLI、code-mode host 与 bwrap。仅选择实际打包的 Cargo workspace
目标，使用上游锁文件校验依赖与 V8/ripgrep 制品，移除可执行文件的调试段。
构建二进制已报告 `codex-cli 0.162.1`。bundled Git 2.55.0 和 uv/uvx 0.12.13
保持原有版本。

启动器测试通过 11 项，覆盖原 profile 指令保留、bundle 规则注入、工作区临时
目录、自定义运行目录与既有账号保护行为。三个运行时回归测试全部通过：删除兼容锁后仍拒绝第二写入者、服务过载
重试语义、已有写入者持锁时仍能打开并扩展读取连接池。运行时文件清单和
分片清单已核对，活动链接已原子切换到 0.162.1。未运行完整上游测试套件。

## 回退

```bash
./bin/update-codex-runtime --rollback 0.156.1
```

原运行时和分片归档保留。升级或回退后需要重启已有 Codex 窗口。

最终 `codex-profile doctor` 与 `claude-profile doctor` 均通过。修复了只搜索
profile/宿主 PATH 而漏掉 bundled `bin/uvx` 的探测问题；Lean runner 0.12.13
已正确识别。此主机的可选 zsh-fork 后端仍不可用，普通 shell 不受影响。
两个实际启动器分别报告 Codex 0.162.1 与 Claude Code 2.1.296；Claude 已与
官方发行清单核对 SHA-256。原有未提交改动保留，本次未提交或推送 Git。

实际交互启动验证：`codex-profile run zcwang-gpt` 原先因后台 daemon 控制 socket 未就绪失败。启动器现统一传入 `--no-daemon`（含 resume/fork），使用内嵌服务，不启动后台 daemon。已实际进入 v0.162.1 交互界面，启动器 11 项回归测试通过。

后续完整启动验证：经用户确认保存 `/volume/math/users/zcwang` 的目录信任，zcwang-gpt 完成初始化，主界面显示 GPT-5.6-Sol；通过 Ctrl-C 正常关闭交互进程，退出码为 0。
