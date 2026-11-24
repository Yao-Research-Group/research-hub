# 课题组科研协作与周报同步协议（v3.0 · 强白名单可审计版）

> 本协议中心位于 `~/.yao-research/hub/protocol/CURRENT.md`。**绝不触碰任何全局 AI 规则文件（`~/.claude`/`~/.codex`/`~/.gemini`/`~/.cursor`）**；一切同步严格围栏在本人亲口登记的白名单科研目录内，默认仅授权导师 `taoyao2005` 可见（可自愿通过 `--collaborators` 加同组队友互看代码）。

---

## 1. 安全围栏与两档同步模式

- **目录围栏（Hard Directory Jail）**：
  - 只有通过 `yao_sync.py init --project-dir "<科研目录>"` 登记进本地 `0600` 策略文件 `~/.yao-research/local-policy.json` 的目录才能触发同步；任何跨目录、未登记目录、符号链接或 `..` 路径逃逸直接硬拦截（退出码 `5`）。
- **两种安装模式（均零全局入侵）**：
  1. `--mode manual`（默认）：完全不改任何 `AGENTS.md` / `CLAUDE.md`，当做清晰好用的纯命令行上传工具使用。
  2. `--mode project-rule`：**仅在授权的白名单科研目录内**写入 `<project_dir>/AGENTS.md`（及同目录独立 `CLAUDE.md`），让 AI 在这个科研项目里推进阶段工作或收尾时顺滑调用 `yao_sync.py`，完全无感且绝不波及机器上的其他目录或私人对话。
- **两种推送确认策略（`--confirm-mode`）**：
  1. `confirm`（默认）：`preview` 展示待传清单与汇报明文，用户确认后带 `--confirm` 推送。
  2. `auto-in-allowlist`：白名单目录内上传 `progress/`、`code/`、`weekly/<W>/artifacts/`、`sessions/` 时免交互无感直推；**硬例外**：凡新增或修改学生本人判断页 `weekly/*/judgment.md`，一律仍需 `--confirm`（确保符合“判断页必须由本人确认”的要求）。

---

## 2. 可审计资产与分层护栏（不再误杀代码与图表）

- **私有库允许目录**：
  - `progress/YYYY-MM-DD.md`：日常今日进展摘要
  - `weekly/YYYY-Www/{evidence.md,judgment.md,report.md,meta.yaml}`：双轨周报（AI 证据页 + 本人判断页）
  - `weekly/YYYY-Www/artifacts/<子路径>`：本周实验图表（`.png`/`.jpg`/`.pdf`）、结果表（`.csv`/`.json`）、关键脚本
  - `code/<子路径>`：白名单项目同步的代码与配置，供导师 AI 直接拉取并扫视审计
  - `sessions/YYYY-MM-DD*.md`：（可选）本白名单课题目录内经脱敏的工作讨论摘要
- **Tier-1 硬拦截（`audit_paths` 与 Git `pre-commit` 双拦截）**：
  - 白名单外路径、符号链接、`.env*`/`*.pem`/`*.key`/`*secret*`/`*credential*`/`*cookie*`、`__pycache__`/`.venv`/`wandb` 等缓存目录、模型权重与压缩包（`.pt`/`.ckpt`/`.safetensors`/`.parquet`/`.zip`/`.tar.gz`）、单文件超过 **10 MiB**、以及文本内真实私钥/API Token/带签名链接。
- **Tier-2 软审计提醒（输出 `[WARN]`，不阻塞合法上传）**：
  - 汇报 `.md` 正文出现 `/Users/...`、`E:/...`、`OneDrive` 等**本机死链**（提示改为 `attach`/`sync-code` 后的仓库相对路径）；
  - 汇报正文遗留 **AI 草稿提示语**（如 `本地审阅草稿`、`先生成 diff 并由用户确认`、`请确认是否上传`）；
  - `evidence.md` 超过 6,000 字符（约 2 页）、`judgment.md` 超过 800 字符或暂缺、以及汇报正文出现邮箱/手机号 PII 时提醒确认。

---

## 3. 日常上传与周一双轨周报标准流程（截止周一 19:00）

### 第一步：把本周代码、图表和结果文件送进私有库（不再写本机死链！）

```bash
# 附加实验图表/CSV结果到 weekly/<week>/artifacts/（自动打印可用于 evidence.md 的相对链接）
python3 ~/.yao-research/hub/scripts/yao_sync.py attach --from-dir "<当前科研目录>" figures/eval.png results/metrics.csv

# 同步当前项目的干净源码树到 code/（自动跳过 .git / .venv / __pycache__ / gitignore / 密钥 / 大权重）
python3 ~/.yao-research/hub/scripts/yao_sync.py sync-code --from-dir "<当前科研目录>"
```

### 第二步：生成双轨周报（AI 写证据 + 人写判断）

1. **`weekly/<YYYY-Www>/evidence.md`（AI 生成，≤ 2 页）**：严格按照 [`prompts/student-evidence.md`](prompts/student-evidence.md) 从代码提交、结果文件、图表与实验日志提取事实；**每条结论附仓库内相对链接**（如 `[metrics.csv](artifacts/metrics.csv)`、`[train.py](../../code/<项目>/train.py)`），必填“失败的尝试”与“矛盾/不确定之处”。
2. **`weekly/<YYYY-Www>/judgment.md`（学生本人撰写/口述整理，≤ 半页约 300 字）**：按 [`prompts/student-judgment-guide.md`](prompts/student-judgment-guide.md) 回答 5 个核心问题（重要结论、假设认知变化、下一步与明确放弃项、最需解答的一个问题、亲自核对了哪些证据内容），并确认 `weekly/<YYYY-Www>/meta.yaml` 中的 `meeting_request`（`none` / `group` / `1on1` / `thu_review` / `sat_product`）。

### 第三步：预览或一条龙上传

```bash
# 查看文件清单、汇报明文与 Tier-2 提醒
python3 ~/.yao-research/hub/scripts/yao_sync.py preview --from-dir "<当前科研目录>"

# 一条龙附加产物 + 同步代码 + 提交推送（confirm 模式或含 judgment.md 时加 --confirm）
python3 ~/.yao-research/hub/scripts/yao_sync.py upload --from-dir "<当前科研目录>" --attach results/metrics.csv --sync-code . --mode weekly --confirm
```
