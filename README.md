# Yao Research Group · 科研进展手动同步工具（v2.0.1 隐私优先版）

上海交大姚韬老师课题组个人私有科研进展同步工具。**完全手动触发、绝不自动记录或静默上传、绝不修改任何全局或项目 AI 规则文件（`AGENTS.md`/`CLAUDE.md`）；只上传你亲眼确认过的几行科研进展纯文本摘要。**

- **六项隐私硬防线**（详情与宿主权限锁配置见 [`docs/PRIVACY.md`](docs/PRIVACY.md)）：
  1. **零自动规则注入**：不改 `~/.claude`/`~/.codex`/`~/.gemini`/`~/.cursor` 全局规则，也不向项目目录写入自动收尾触发块。平时对话 100% 不会自动同步。
  2. **授权目录 + 最小纯文本**：初始化时绑定你亲口指定的一个科研工作目录到纯本地白名单（`~/.yao-research/local-policy.json`）；私有库只允许 `progress/YYYY-MM-DD.md`（今日进展）与 `weekly/YYYY-Www/report.md`（≤64 KiB），源码、数据、图片、PDF 及本机环境遥测一律拒收。
  3. **协作者最小授权与平台边界**：支持 `--collaborators` 自主指定授权对象（如 `--collaborators taoyao2005` 仅授权导师，默认 `taoyao2005` 与 `treerobin06`）。GitHub 组织库自动设只读 `pull`；**GitHub 个人私有库因平台限制仅支持标准协作者权限**（汇总脚本 `secretary_collect.py` 仅执行只读 `git pull`），本人随时可在 GitHub Settings 一键撤销授权。
  4. **Git `pre-commit` 硬钩子**：提交前自动拦截外部本机路径（如 `OneDrive`、`/Users/...`）、密钥、邮箱、手机号与超限文件。
  5. **主动触发 → 看明文 → 确认才推**：必须先 `preview` 展示完整待传文字，经你逐字 review 并同意后带 `--confirm` 才能推送。
  6. **不静默更新脚本**：绝不后台自动 `git pull` 覆盖本地代码；升级需显式运行 `check-update` / `upgrade`。

---

## 给 AI 助手的安装与使用流程

> **导师提示**：若您是导师（如 `taoyao2005`），只需接受同学们私有库的协作者邀请，**无需安装学生端**。

1. **克隆本公开只读仓库到本地**（Windows PowerShell 将 `~` 换为 `$HOME`、`python3` 按需换为 `python`）：
   ```bash
   git clone https://github.com/Yao-Research-Group/research-hub.git ~/.yao-research/hub
   ```
   *(若本地已存在，请先运行 `git -C ~/.yao-research/hub fetch && git -C ~/.yao-research/hub log HEAD..origin/main --oneline` 经用户确认后再 pull。)*
2. **检查 GitHub CLI 登录**：运行 `gh auth status`。若未安装或未登录，先引导用户 `gh auth login`（或改用 [`ONBOARDING.md`](ONBOARDING.md) 3 步网页建私库）。
3. **请用户明确输入 1 个科研工作目录路径**（AI 不可自行猜测），执行初始化（不会修改任何全局或项目 AI 规则文件；可用 `--collaborators taoyao2005` 仅邀请导师）：
   ```bash
   python3 ~/.yao-research/hub/scripts/yao_sync.py init --project-dir "<用户明确输入的科研目录>"
   ```
   *(安装完成后，可温和询问用户：**是否需要为你设一个纯本地提醒（例如每周一晚 7 点前只提醒你自己手动整理周报，绝不自动上传）？** 由用户自主决定。)*
4. **后续每次需要同步时（完全由用户主动口头触发，严格两步确认）**：
   - 仅当用户主动说“整理今天科研进展并预览”时，按 [`protocol/CURRENT.md`](protocol/CURRENT.md) 在 `~/.yao-research/repo/progress/YYYY-MM-DD.md`（或 `weekly/<week>/report.md`）写入几行纯文本进展摘要（绝不含外部路径或隐私）。
   - 先运行预览并**向用户展示完整明文内容**：
     ```bash
     python3 ~/.yao-research/hub/scripts/yao_sync.py preview --from-dir "<当前科研目录>"
     ```
   - **必须等用户亲自 review 并明确回复同意后**，再执行带确认标志的推送：
     ```bash
     python3 ~/.yao-research/hub/scripts/yao_sync.py push --confirm --from-dir "<当前科研目录>"
     ```

---

## 私有库目录结构（严格白名单）

```text
<你的GitHub>/yao-research-memory/     # Private（仅本人与授权协作者可见；汇总脚本仅只读拉取）
├── .gitignore                        # 本地拦截密钥/权重/数据/临时文件
├── README.md                         # 仓库说明
├── profile.md                        # 极简档案（仅 GitHub ID 及可选称呼/课题，零本机路径与环境遥测）
├── progress/
│   └── YYYY-MM-DD.md                 # 今日进展纯文本摘要（≤64 KiB）
└── weekly/
    └── YYYY-Www/
        ├── meta.yaml                 # 极简周报元信息（无本机路径）
        └── report.md                 # 本周进展汇总（≤64 KiB）
```
