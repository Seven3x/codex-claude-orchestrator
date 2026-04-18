# Codex + Claude Code Orchestrator（中文说明）

这个仓库的目标很简单：

- 让 Codex 只做规划、分发和最终少量复核
- 让 Claude 负责低风险、本地、耗时的执行任务
- 让宿主机上的 `cco` 服务统一管理 worker、状态、日志和回调

核心思路不是“让 Codex 在自己的沙箱里硬跑所有事”，而是：

1. Codex 只发送最小任务描述
2. 宿主机常驻的 `cco server` 接收请求
3. `cco` 在宿主环境里启动 Claude worker
4. Claude 完成本地任务并写结果
5. `cco` 记录状态、日志，并在需要时再唤醒 Codex

## 适合什么任务

适合交给 Claude 的任务：

- 本地代码搜索
- 简单调试
- 机械式小改动
- 测试执行
- 日志总结
- 本地长时间运行或验证

不适合直接交给 Claude 的任务：

- 架构设计
- 大范围 root cause 分析
- 需要外部资料或最新文档
- 高风险重构
- 方法论、论文结论、公平性判断

## 整体架构

推荐部署方式是“宿主机常驻服务”：

- `cco server` 常驻运行
- Codex 不直接碰 Claude CLI
- Codex 只向 `cco` 发送最小字段：
  - `repo_root`
  - `kind`
  - `task`
  - `paths`
  - `checks`
  - `codex_thread_id`
- `cco` 自己负责：
  - 启动 Claude
  - 写 job 状态
  - 记录日志
  - 接收 SessionEnd hook
  - 必要时恢复 Codex

## 安装

### 1. 安装本仓库

```bash
python -m pip install -e .
```

如果你平时把 `cco` 装在 conda `base` 环境里，可以直接用：

```bash
./scripts/cco-base --help
```

它会优先用现成的 `cco`，否则自动尝试常见的 conda/miniconda 路径。

### 2. 安装 Claude Code CLI

确保：

- `claude` 在 `PATH` 中
- Claude 已可认证使用
- 本机代理或 `ANTHROPIC_BASE_URL` 可达

默认行为：

- worker 会启用 `--dangerously-skip-permissions`
- worker 会优先通过 `systemd-run --user` 在宿主机上启动

可以通过环境变量覆盖：

```bash
export CCO_CLAUDE_SKIP_PERMISSIONS=0
export CCO_CLAUDE_LAUNCHER=subprocess
```

### 3. 安装 MCP 依赖

本仓库已经直接依赖 `mcp`，安装完成后会提供：

```bash
cco-mcp
```

## 启动服务

### 启动宿主机 `cco` 服务

```bash
./scripts/cco-base server --host 127.0.0.1 --port 8765
```

### 安装固定的 user service

不要再反复用 `systemd-run --user` 起很多临时 `cco-*` 服务。
仓库里现在提供了一个安装脚本，会直接生成并启动固定的 `cco.service`：

```bash
bash ./scripts/install-cco-service
```

默认会创建：

- `~/.config/systemd/user/cco.service`
- 监听 `127.0.0.1`
- 端口 `8765`

如果你想改名字或端口，可以先设置：

```bash
export CCO_SERVICE_NAME=cco
export CCO_SERVICE_HOST=127.0.0.1
export CCO_SERVICE_PORT=8765
bash ./scripts/install-cco-service
```

常用命令：

```bash
systemctl --user status cco.service
systemctl --user restart cco.service
curl -s http://127.0.0.1:8765/health
```

服务提供三个接口：

- `GET /health`
- `POST /dispatch`
- `POST /claude-session-end`

这个服务本身不绑定单个仓库。
仓库由每次 `/dispatch` 请求中的 `repo_root` 决定。

## 通过 HTTP 分发任务

示例：

```bash
curl -s -X POST http://127.0.0.1:8765/dispatch \
  -H 'Content-Type: application/json' \
  -d '{
    "repo_root": "/home/roxy/Deepc",
    "kind": "run",
    "task": "List all CSV filenames in this repository, write the sorted relative paths to /tmp/deepc_repo_csv_files.txt, and write the required worker result JSON.",
    "paths": ["."],
    "checks": ["rg --files -g '\''*.csv'\''"],
    "codex_thread_id": "",
    "no_codex_resume": true
  }'
```

`/dispatch` 只需要最小字段：

- `repo_root`
- `kind`
- `task`
- `paths`
- `checks`
- `codex_thread_id`

可选字段：

- `no_codex_resume`
- `requires_web`
- `force`

## 通过 MCP 调用

本仓库提供一个很薄的 MCP server：

```bash
cco-mcp
```

它暴露的工具有：

- `cco_health`
- `cco_dispatch`
- `cco_job_status`

### `cco_dispatch`

负责把最小任务发送给宿主机 `cco` 服务。

主要参数：

- `repo_root`
- `kind`
- `task`
- `paths`
- `checks`
- `codex_thread_id`

### `cco_job_status`

直接读取某个 job 目录，返回：

- `meta`
- `worker_result`
- `cco_monitor.log` 尾部
- `claude_output.log` 尾部
- `claude_stdout.log` 尾部
- `claude_stderr.log` 尾部

## Job 目录结构

每个任务都会落在：

```text
.cco/jobs/<job_id>/
```

常见文件：

- `meta.json`
  当前最新状态
- `worker_prompt.txt`
  发给 Claude 的 worker prompt
- `worker_result.json`
  Claude 最终写回的结果 JSON
- `cco_monitor.log`
  `cco` 服务端主动监控到的生命周期日志
- `claude_output.log`
  Claude 的完整组合输出日志
- `claude_stdout.log`
  Claude 标准输出
- `claude_stderr.log`
  Claude 标准错误
- `codex_resume_prompt.txt`
  若需要恢复 Codex，这里保存恢复提示词
- `codex_resume_response.txt`
  若恢复了 Codex，这里保存 Codex 响应

## 日志说明

### `cco_monitor.log`

这是服务端视角的状态日志，典型内容包括：

- monitor 启动
- worker pid 是否还活着
- systemd unit 状态
- `worker_result.json` 是否已经生成
- 是否通过 hook 或 fallback 完成 finalize

### `claude_output.log`

这是最适合直接查看的 Claude 输出日志。
它会把 Claude 的全部输出统一保存在一个文件里。

如果你还想区分标准输出和标准错误，也可以看：

- `claude_stdout.log`
- `claude_stderr.log`

## 推荐工作流

推荐把这套系统当作“宿主机上的执行服务”来用：

1. 宿主机启动 `cco server`
2. Codex 通过 HTTP 或 MCP 调 `cco_dispatch`
3. `cco` 在宿主环境起 Claude worker
4. `cco` 主动监控 worker 并写日志
5. Claude 完成任务后写 `worker_result.json`
6. 如果 `needs_codex_review=false`，就不再恢复 Codex
7. 如果需要复核，再由 `cco` 恢复 Codex

## 已解决的关键问题

这版已经处理了之前几类常见问题：

- Claude 不再默认继承当前 Codex 沙箱
- worker 可优先通过宿主 `systemd-run --user` 启动
- runtime 目录放在 `/tmp/cco-claude-runtime/<job_id>`
- `.cco` 目录不再建议纳入版本控制
- 服务端会主动监控 worker，而不是只依赖 hook
- 即使目标仓库没有 `.claude/settings.json`，也会自动注入默认 `SessionEnd` hook

## 建议

- 把 `.cco` 保持在 `.gitignore` 中
- 把 `cco server` 做成宿主机常驻服务
- 让 Codex 只做“最小任务分发”和“必要时复核”
- 本地低风险执行尽量全部走 Claude worker
