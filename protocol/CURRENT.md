# 课题组科研协作与周报同步协议（v3.1.0 · 安全加固版）

> 本协议中心位于 `~/.yao-research/hub/protocol/CURRENT.md`。**绝不触碰任何全局 AI 规则文件；试用阶段默认且推荐只开放 `--mode manual` + `confirm` 确认码绑定模式（`--confirm-mode auto-in-allowlist` 与 `sessions/` 上传在试用期暂停）。**

---

## 1. 安全围栏与目录隔离规则

- **课题目录校验（拒绝过宽目录）**：`init --project-dir` 必须精确到具体的课题子目录，程序强制拒绝 `/`、`$HOME`、`~/Desktop`、`~/Documents`、`~/Downloads`、`~/Projects`、`~/dev` 及私有库/协议中心祖先目录（退出码 `2`）。
- **试用期锁定 `confirm` + `--confirm-hash` 确认码**：
  - 任何本地变更都必须先跑 `preview` 展示全部清单与**完整未截断正文**，生成 12 位 SHA256 确认码（`last_preview_hash`）；
  - `push` 时**必须携带完全匹配的 `--confirm-hash <12位码>`**。未跑 `preview`、预览后修改了文件、或只传旧 `--confirm` 一律硬拦截（退出码 `6`）。
- **来源清单（`provenance.json`, `0600`）**：
  - `code/*` 与 `weekly/*/artifacts/*` 中的任何新增/修改文件，**必须经过 `sync-code` 或 `attach` 从白名单目录导入**并登记哈希；直接手放进仓库 `code/` 或绕过工具篡改会在 `preview`/`push` 阶段被硬拦截（退出码 `5`）。
- **多项目分库建议**：若你同时推进多个课题且需与不同队友共享，**务必为不同项目指定独立 `--repo-name` 与 `--repo-dir`**，避免同一仓库的协作者互相看见其他课题代码。

---

## 2. 可审计资产导入与分层拦截

- **`sync-code`（严格纯源码，默认仅已跟踪文件）**：
  - **绝不上传**任何点开头文件/目录（`.netrc`、`.env`、`.npmrc`、`.pypirc`、`.claude/`、`.vscode/`、`.cursor/`、`.git/`）；
  - **仅同步明确源码扩展名**（`.py`, `.ipynb`, `.sh`, `.yaml`, `.yml`, `.toml`, `.ini`, `.cfg`, `.md`, `.tex`, `.bib`, `.c/.cpp/.cu/.rs/.go/.ts/.js`, `.sbatch`, `.txt` 等，外加 `Makefile`/`Dockerfile`/`requirements.txt`/`pyproject.toml`）；
  - 若源目录在 Git 仓库内，默认仅拉取 `git ls-files` 已跟踪源码（加 `--include-untracked` 才扫未跟踪源码）。
  - **数据表（`.csv`/`.tsv`/`.json`/`.jsonl`）、实验图（`.png`/`.jpg`/`.pdf`）** 绝不会在 `sync-code` 中顺带上传，必须用 `attach` 逐个显式指定！
- **`attach`（显式附加实验图与结果表）**：用于把白名单目录内指定结果表、图和 PDF 放入 `weekly/<W>/artifacts/` 或 `code/`。
- **Tier-1 硬拦截**：私钥头、GitHub/OpenAI/HuggingFace/AWS/WandB Token、带密码连接串 URL、硬编码 `password = "..."`、`api_key = "..."`、飞书/Slack/钉钉/企微 Webhook、`.netrc`/`kaggle.json`/凭据 JSON、大模型权重与压缩包（`.pt`/`.safetensors`/`.parquet`/`.zip`）、单文件 > **10 MiB**、18 位身份证号（全文件拦截）及数据表（`.csv`/`.tsv`/`.jsonl`）中的手机号。
- **Tier-2 软提醒**：汇报 `.md` 正文出现本机绝对路径死链（`/Users/...`、`E:/...`）、AI 草稿提示残留、或正文中邮箱/手机号，输出 `[WARN]` 提醒本人确认。

---

## 3. 日常与周一双轨周报标准三步（截止周一 19:00）

1. **按需导入源码、图表与撰写周报**：
   ```bash
   # 仅同步干净源码（可加 --delete 同步清理源端已删文件）
   python3 ~/.yao-research/hub/scripts/yao_sync.py sync-code --from-dir "<当前科研目录>" --source "<课题子目录>"

   # 显式附加指定实验图与结果表
   python3 ~/.yao-research/hub/scripts/yao_sync.py attach --from-dir "<当前科研目录>" results/metrics.csv figures/eval.png
   ```
   按 [`prompts/student-evidence.md`](prompts/student-evidence.md) 与 [`prompts/student-judgment-guide.md`](prompts/student-judgment-guide.md) 整理 `weekly/<W>/evidence.md`（≤ 2 页，全相对链接）与 `weekly/<W>/judgment.md`（本人判断 ≤ 300 字）。

2. **预览清单与完整明文，拿到 `confirm-hash`**：
   ```bash
   python3 ~/.yao-research/hub/scripts/yao_sync.py preview --from-dir "<当前科研目录>"
   # 如需逐行检查代码文件内容，可加 --full-code
   ```

3. **经本人确认后，携带精确 `confirm-hash` 提交推送**：
   ```bash
   python3 ~/.yao-research/hub/scripts/yao_sync.py push --confirm-hash <preview输出的12位确认码> --from-dir "<当前科研目录>"
   ```
