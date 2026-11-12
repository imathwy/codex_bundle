# Codex bundle 0.164.0 升级记录

从 0.163.0 移植到官方 `rust-v0.164.0`，上游提交
`6aaac694803004a63c6f393d153c96d22521a6b9`。保留账号隔离、共享会话、写入租约、
活跃写入者自动 fork、provider 回退、SQLite 启动和过载重试等现有补丁。完整补丁
随运行时保存在 `LOCAL_PATCHES.patch`。

自动流程在 cherry-pick 本地补丁时只在 `codex-rs/state/src/sqlite.rs` 冲突，
人工合并结果：

- 保留 bundle 的 `CODEX_LOG_DB_PATH` 覆盖，并置于上游新增的 logs home 选择之前，
  每个会话仍写自己的 `logs_2-*.sqlite`；
- 保留 60 秒 busy timeout、迁移短路（`runtime_migrations_are_current`）和 120 秒
  pool acquire 窗口；
- 上游 0.164.0 的 `connect_once` 已自带本 bundle 先前补的 pool 初始化错误直传
  （mpsc + `tokio::select!`）、`auto_vacuum = INCREMENTAL` 与免写锁 WAL 处理，
  并新增 `retry_on_busy`，因此这一段改用上游实现。

构建源码、工具链、缓存、临时文件和日志均位于 bundle `.work/`。上游把工具链 pin
到 1.99.1（rustup 自动安装），构建使用 `CARGO_BUILD_JOBS=32` 和上游 package
builder。`bin/update-codex-runtime` 原先在仓库外查询 `rustc --version`，会把
provenance 写成默认工具链；已改为在 `codex-rs` 内查询，并据此修正本次
`BUILD_INFO.md` 后重算运行时清单、分片归档与 bundle 清单。

0.163.0 运行时及分片归档保留，可用以下命令回退：

```bash
./bin/update-codex-runtime --rollback 0.163.0
```

升级或回退后，重启已有 Codex 窗口以加载新版本。

## 同批次修复：profile auth guard 锁等待

`codex-profile: could not verify profile auth guard; it will retry` 在并发会话下
频繁出现（dispatch 日志中累计 2435 条，且是唯一的 note）。原因是
`guard_auth_once()` 只用 `flock -w 1` 等 `auth.lock`——脚本里其他持锁者都是
`-w 5`——并且在持锁期间无条件调用 `codex-history auth-identity`，即使文件与快照
字节相同。同一 profile 的多个会话在 auth.json 被写回时同时触发检查，串行等待超过
1 秒即全部放弃；watcher 随后还会刷新签名，使提示里的 "it will retry" 并不成立。

`bin/codex-profile` 的改动：

- 字节与快照相同即判定通过，不再启动 identity 进程；
- 锁等待改为 `CODEX_PROFILE_GUARD_LOCK_TIMEOUT_SECONDS`（默认 5），并加入 usage、
  环境变量白名单与启动校验；
- 未判定时保留上次已验证的签名，下一轮真正重开检查，连续失败只提示一次；
- launcher 退出后的最后一次检查改走 `guard_profile_until_verified()`，3 次静默
  重试后才提示。

回滚副本：`backups/guard-lock-fix-20261027T1817Z/bin/codex-profile`。

## 验证结果

- A/B 复现：12 个兄弟 guard 并发、identity 工具耗时 0.25 秒时，旧代码输出 8 条
  提示，新代码 0 条。
- `tests/`（`test_codex_profile`、`test_codex_history`）17 项通过，含两个新增
  回归测试：字节未变不调用 identity 工具、锁被占用时静默重试且只提示一次。
- `codex-profile run zcwang-gpt -- --version` 报告 `codex-cli 0.164.0`，stderr 干净；
  同时仍有 5 个 0.163.0 会话在跑。
- `codex-profile doctor` 退出码 0（13 个 profile、受保护的 trusted state、
  运行时哈希、相对链接、权限均通过）。运行前清掉了 `profiles/default` 下 10 月 24 日
  残留的 `.codex/tmp/arg0-linux/codex-arg0CfGgzH` 临时 shim 目录，它指向
  `.work/runtime-cache-0` 因而被 escaping-symlink 检查拒绝。
- bundle 清单、运行时清单、分片归档清单校验全部通过。
- 发布构建成功；未运行完整上游测试套件，构建中的 unused-import 警告不影响产物。
  未提交或推送 Git。
