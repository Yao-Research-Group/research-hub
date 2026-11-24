# Yao Research Group · 科研进展与代码图表协作同步工具（v3.0）

上海交大姚韬老师课题组个人私有科研同步工具。**只把权限死锁在用户亲口指定的科研目录内，绝不碰全局 AI 规则；在白名单目录内支持顺滑上传进展、代码（`code/`）、实验图表与结果表（`artifacts/`），让导师和校外科学家 / 导师 AI 能直接拉取抽查并做证据核验。**

- **核心设计（详细护栏与诚实边界见 [`docs/PRIVACY.md`](docs/PRIVACY.md)）**：
  1. **绝不碰全局，只在授权目录内围栏**：永远不修改 `~/.claude`、`~/.codex`、`~/.gemini`、`~/.cursor` 全局规则；初始化强制绑定 1 个科研工作目录到纯本地白名单（`~/.yao-research/local-policy.json`，权限 `0600`），所有跨目录、符号链接或 `..` 逃逸一律硬拦截（退出码 `5`）。
  2. **默认仅邀导师 `taoyao2005`**：彻底移除默认第三人账号，只授权导师 `taoyao2005` 查看。若同课题同学需要互看代码（如 PFN-MDP 小组），可通过 `--collaborators taoyao2005,队友GitHub` 自主添加。（注：GitHub 组织库自动设只读 `pull`；个人私有库因平台限制采用标准协作者邀请并自动兼容 HTTP 422，本人可随时在 GitHub Settings 一键撤销。）
  3. **可审计科研资产放开，分层护栏不误杀**：私有库支持上传 `.py`/源码、`.png`/`.jpg`/`.pdf` 实验图表、`.csv`/`.json` 结果表、`progress/YYYY-MM-DD.md`、`weekly/<W>/{evidence.md,judgment.md,artifacts/}` 及白名单内脱敏讨论记录 `sessions/`；Tier-1 硬拦密钥/Token/`.env*`/大模型权重（`.pt`/`.ckpt`/`.safetensors`/`.parquet`/`.zip`）/ >10 MiB 大文件，Tier-2 仅软提示本机死链（`/Users/...`、`E:/...`）与 AI 草稿话术残留。
  4. **白名单内可选“无感同步” vs “纯手动”，`judgment.md` 永远本人确认**：默认 `--mode manual --confirm-mode confirm`；如希望在授权科研目录里更省心，可自选 `--mode project-rule --confirm-mode auto-in-allowlist`（只往该科研目录写 `AGENTS.md`，进度/代码无感直推，但凡涉及学生本人判断页 `weekly/*/judgment.md` 一律仍须展示明文并带 `--confirm`）。

---

## 一、给 AI 助手的 3 步安装流程

> **导师端说明**：导师（`taoyao2005`）只需接受同学私有库邀请，运行 `scripts/advisor_collect.py --accept-invites --sync --digest` 即可在本地直接拉取全组代码、图表与周报并生成 AI 预读包，**无需安装学生端**。

1. **克隆本公开协议仓库并核对 GitHub CLI 登录**：
   ```bash
   git clone https://github.com/Yao-Research-Group/research-hub.git ~/.yao-research/hub
   gh auth status
   ```
   *(若本地已克隆过，先运行 `git -C ~/.yao-research/hub fetch && git -C ~/.yao-research/hub log HEAD..origin/main --oneline`，经用户确认后执行 `upgrade`。未装/未登录 `gh` 可参考 [`ONBOARDING.md`](ONBOARDING.md)。)*

2. **请用户明确指定 1 个科研工作目录，并让用户二选一同步模式**（AI 不得代选或改全局规则）：
   - **选项 A（本项目内无感顺滑，推荐平时高频迭代课题）**：仅在该科研目录写入项目级 `AGENTS.md`（不碰全局），目录内日常进展与代码/图表自动同步，判断页仍需确认：
     ```bash
     python3 ~/.yao-research/hub/scripts/yao_sync.py init \
       --project-dir "<用户指定的科研目录>" \
       --mode project-rule \
       --confirm-mode auto-in-allowlist
     ```
   - **选项 B（纯手动零文件侵入，最克制）**：不写入任何 `AGENTS.md`/`CLAUDE.md`，只在用户主动开口时执行 `upload` / `preview` / `push --confirm`：
     ```bash
     python3 ~/.yao-research/hub/scripts/yao_sync.py init --project-dir "<用户指定的科研目录>"
     ```
   *(若同课题队友需要互看代码，加 `--collaborators taoyao2005,<teammate_github>`。)*

3. **日常与周一双轨周报（19:00 截稿）一条龙上传**：
   ```bash
   # 1) 附加本周实验图表 / CSV 结果（自动生成可直接放进 evidence.md 的相对路径）
   python3 ~/.yao-research/hub/scripts/yao_sync.py attach --from-dir "<当前科研目录>" results/metrics.csv figures/eval.png

   # 2) 同步当前项目的干净代码树到 code/（自动排除 .git/.venv/缓存/密钥/权重）
   python3 ~/.yao-research/hub/scripts/yao_sync.py sync-code --from-dir "<当前科研目录>"

   # 3) 预览待传清单、汇报明文与 Tier-2 死链/草稿检查
   python3 ~/.yao-research/hub/scripts/yao_sync.py preview --from-dir "<当前科研目录>"

   # 4) 或一条龙附加、同步代码并推送（confirm 模式或含 judgment.md 时加 --confirm）
   python3 ~/.yao-research/hub/scripts/yao_sync.py upload \
     --from-dir "<当前科研目录>" \
     --attach results/metrics.csv \
     --sync-code . \
     --mode weekly \
     --confirm
   ```

---

## 二、导师端汇总与 AI 预读抽查

```bash
python3 ~/.yao-research/hub/scripts/advisor_collect.py --accept-invites --sync --digest
```

汇总工作区 `~/.yao-research-advisor/`（权限 `0700`）会输出：
- `spokes/<owner>__<repo>/`：全组私有库的完整本地克隆（导师 AI 可直接读取 `code/` 与 `weekly/<W>/artifacts/` 原始脚本和图表）。
- `digests/<YYYY-Www>/advisor-preread-input.md`：内嵌 [`protocol/prompts/advisor-preread.md`](protocol/prompts/advisor-preread.md) 预读提示词、每名学生的本地路径、附件/代码计数、提交记录与质量自检标旗（`missing_judgment`、`has_local_abs_paths`、`evidence_too_long`、`ai_draft_leak_phrase`）。
- `digests/<YYYY-Www>/advisor-report.html` & `team-digest.md` & `status.json`。

---

## 三、私有库目录结构

```text
<你的GitHub>/yao-research-memory/     # Private（默认仅本人与导师 taoyao2005 可见）
├── .gitignore                        # 拦截密钥 / .env* / 模型权重 / 大归档 / 环境缓存
├── README.md                         # 仓库说明
├── profile.md                        # 极简成员档案（无本机环境遥测）
├── progress/
│   └── YYYY-MM-DD.md                 # 日常今日科研进展摘要
├── code/
│   └── <project>/...                 # 白名单目录同步的源码/配置/结果表（供导师 AI 扫视审计）
├── sessions/
│   └── YYYY-MM-DD-*.md             # （可选）本白名单目录脱敏讨论摘要
└── weekly/
    └── YYYY-Www/
        ├── meta.yaml                 # 课题、阻塞、meeting_request 等元信息
        ├── evidence.md               # AI 生成的事实证据页（≤2 页，全部相对链接，含失败尝试）
        ├── judgment.md               # 本人亲自思考确认的判断页（≤300 字）
        └── artifacts/                # 本周实验图表（PNG/JPG/PDF）、CSV 结果与关键脚本
```
