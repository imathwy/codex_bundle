# Codex bundle 0.163.0 升级记录

从 0.162.1 移植到官方 `rust-v0.163.0`，上游提交
`83086188ae0614bd882818ff1aa23f3194962012`。保留账号隔离、共享会话、写入租约、
活跃写入者自动 fork、provider 回退、SQLite 启动和过载重试等现有补丁。

移植时唯一冲突位于 `thread_processor.rs`：保留上游新增的排除 ID 数量限制，
并保留 bundle 的共享会话环境变量常量。完整补丁随运行时保存在
`LOCAL_PATCHES.patch`。

所有 profile 的启动入口默认传入 `-c 'tui.raw_output_mode=false'` 和
`-c 'tui.alternate_screen="never"'`，关闭 raw 模式，保持工具输出精简，
并保留终端滚动历史。新会话、resume、fork 和今后创建的
profile 均应用该默认值；仍使用内嵌服务 `--no-daemon`。


构建源码、工具链、缓存、临时文件和日志均位于 bundle `.work/`。
构建使用 Rust 1.95.0 和上游 package builder，复用之前的 Cargo 构建缓存。
0.162.1 运行时及分片归档保留，可用以下命令回退：

```bash
./bin/update-codex-runtime --rollback 0.162.1
```

升级或回退后，重启已有 Codex 窗口以加载新版本和显示设置。

## 验证结果

- 普通 scrollback 启动器测试 3 项通过；确认默认参数包括
  `tui.alternate_screen="never"` 和 `tui.raw_output_mode=false`。
- 实际 `codex-profile run zcwang-gpt -- --version` 报告 `codex-cli 0.163.0`，
  同一 profile 的 `features list` 配置加载成功。
- `codex-profile doctor` 退出码 0；13 个 profile、权限、共享状态链接及运行时校验通过。
- bundle 文件清单、运行时文件清单及分片归档校验通过。
- 发布构建成功；未运行完整上游测试套件。构建中的 unused-import/unused-mut
  警告不影响产物生成。未提交或推送 Git。

最终选择为普通 scrollback；曾验证 raw 模式会展开工具参数与完整返回内容，
依用户选择关闭 raw。当前已打开的窗口可用 `/raw` 切换，重启后应用统一默认值。
