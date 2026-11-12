# Codex bundle 升级至 0.156.1

活动运行时从 0.154.0 升级到官方 `rust-v0.156.1`（提交
`b412ff32c417f855c2b2d1581b77058eed87c84b`），并保留原有账号隔离、共享会话、
持久写入租约、活跃写入者自动 fork、跨 provider 恢复、配置保护及 SQLite 启动优化。
对应源码差异保存在新运行时的 `LOCAL_PATCHES.patch`。

补丁先在精确匹配的官方 0.154.0 源码上还原，再通过 Git 三方合并迁移至
0.156.1。上游把写锁实现移入 `codex-rollout` crate；持久 `.lease` 已适配新的
`io::Result` 接口，并仍通过 `codex-thread-store` 的实际写入路径获取。新版 TUI
启动方法和项目信任类型与本地改动一并保留。

## 构建与验证

- 安装 Rust 1.95.0、独立的 Python 3.12.14 和 `libcap-dev`，以 release 配置构建
  x86_64 GNU/Linux CLI、code-mode host 与 bwrap。
- 使用上游包构建器核对 V8 150.4.0 与 ripgrep 制品；仅移除可执行文件的调试段。
  继续内附 Git 2.55.0 及其源码；系统 Git 和现有 uv/uvx 0.12.13 未更改。
- 构建出的 CLI 报告 `codex-cli 0.156.1`。删除兼容 `.lock` 后仍拒绝第二写入者的
  持久租约回归测试通过（1/1）。运行时、分片归档和顶层 SHA-256 清单均已核对。
- `codex-profile doctor` 通过。诊断前将默认 profile 中一个 9 月 15 日留下、
  无进程占用的 uv 临时构建目录移至 `backups/doctor-uv-temp-20260923/`；
  诊断时去除了当前 Codex 工具会话注入的 `CODEX_VERSION` 变量。
- 可选的 zsh-fork 后端在此主机上仍不可用；普通 shell 和 Codex 启动不受影响。
  未运行完整上游测试套件。已有 Codex 窗口需要退出并重启才会加载新二进制。

## 后续自动更新

```bash
./bin/update-codex-runtime --check
./bin/update-codex-runtime
```

脚本查询官方最新稳定版，在临时源码目录移植当前补丁，用上游构建器构建并验证，
生成可供 `doctor` 恢复的分片归档，最后原子切换活动链接。可以通过
`--version 0.156.1` 指定稳定版本。若补丁冲突或构建失败，脚本保留旧活动版本，
并显示临时源码路径供修复。

## 回退

```bash
./bin/update-codex-runtime --rollback 0.154.0
```

旧运行时及其分片归档仍在 bundle 中。回退会重新生成顶层 SHA-256 清单；
已有窗口同样需要重启。仓库现有未提交修改得到保留，本次没有提交或推送 Git。
