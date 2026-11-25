# Yao Research Group · 科研进展与代码图表协作同步工具（v3.1.0 · 安全加固版）

上海交大姚韬老师课题组个人私有科研同步工具。**严格围栏在具体的科研课题子目录内，绝不碰全局 AI 规则；v3.1.0 按安全复审意见全面收紧凭据扫描、来源追踪、确认码强绑定、导师端防软链与防注入。试用阶段推荐并默认只开放 `manual` + `confirm-hash` 确认模式。**

- **核心机制（完整细节与「诚实边界说明」见 [`docs/PRIVACY.md`](docs/PRIVACY.md)）**：
  1. **绝不碰全局 + 拒绝过宽目录**：永远不读取或修改 `~/.claude`、`~/.codex`、`~/.gemini`、`~/.cursor`；`init --project-dir` 强制拒绝 `/`、`$HOME`、`~/Documents`、`~/Desktop`、`~/Projects` 等顶层目录，必须精确到具体的课题子目录。
  2. **仅邀导师 `taoyao2005` 且记住本地协作者列表**：首次默认仅邀请 `taoyao2005`，并把最终协作者名单持久存进本地 `local-policy.json`，重跑 `init` 不再静默重邀默认账号。（**如实说明**：GitHub **个人私有库**平台层不支持只读 `pull`（实测传 `permission=pull` 报 HTTP 422），已接受邀请的协作者在个人库实际具备**可写 Write/Push 权限**，仅组织库可设只读 `pull`，本人随时可在 GitHub Settings 一键取消。）
  3. **`sync-code` 严格纯源码 + `attach` 显式附表图**：`sync-code` 自动排除所有点开头文件/目录（`.netrc`、`.env`、`.claude/`、`.vscode/`），默认仅同步 Git 已跟踪的明确源码扩展名（`.py`/`.yaml`/`.sh`/`.md` 等）；**`.csv`/`.json`/`.pdf`/`.png` 等数据表与图表只能通过 `attach` 显式点名附加**。所有 `code/` 与 `artifacts/` 文件必须经过本地 `provenance.json`（`0600`）来源与哈希核验。
  4. **全面凭据与 PII 拦截**：Tier-1 硬拦 HuggingFace (`hf_`)、AWS (`AKIA`)、WandB (`wandb_v1_` / 40位 hex)、OpenAI/GitHub Token、带密码数据库连接 URL、硬编码 `password=`/`api_key=`、飞书/Slack/钉钉/企微 Webhook、`.netrc`/`kaggle.json`/凭据 JSON、18 位身份证号，以及数据表中的手机号；并强制把私有库本地 `git config --local` 作者固定为 `@users.noreply.github.com`。
  5. **完整明文 `preview` + `--confirm-hash` 确认码强绑定（试用期暂停免确认与 `sessions/`）**：`preview` 展示全部待传/待删清单及**完整未截断汇报明文**，输出 12 位 `confirm-hash`；`push` 时 `--confirm-hash` 必须严格匹配当前内容摘要且与最近一次预览一致（预览后改动文件立即失效，同步支持文件删除与 60s push 超时）。
  6. **导师端三层防御（`advisor_collect.py`）**：仅接收本地白名单 `allowed_students.json`（`--allow-student`）授权学生的邀请；强制 `git clone -c core.symlinks=false` 并逐段拒绝符号链接与 `120000` 索引项；所有学生正文与代码路径包裹在 `<untrusted_student_data>` 中并声明严禁执行学生代码。

---

## 一、给 AI 助手的 3 步安装流程

> **导师端使用说明**：导师只需先批准学生 GitHub ID 白名单并拉取汇总（**不要安装学生端**）：
> ```bash
> git clone https://github.com/Yao-Research-Group/research-hub.git ~/.yao-research/hub
> python3 ~/.yao-research/hub/scripts/advisor_collect.py --allow-student <学生GitHub账号> --accept-invites --sync --digest
> ```

1. **克隆本公开只读协议库并检查登录**：
   ```bash
   git clone https://github.com/Yao-Research-Group/research-hub.git ~/.yao-research/hub
   gh auth status
   ```
   *(未安装/未登录 `gh` 请参考 [`ONBOARDING.md`](ONBOARDING.md)；更新请先 `check-update` 经本人确认后再 `upgrade`。)*

2. **请学生明确输入 1 个具体的课题子目录（禁止 `/`、`$HOME`、总目录），在试用阶段以 `manual` + `confirm` 初始化**：
   ```bash
   python3 ~/.yao-research/hub/scripts/yao_sync.py init --project-dir "<具体的科研课题子目录>"
   ```
   - 如需要与同课题队友互看代码，追加 `--collaborators taoyao2005,<队友GitHub>`。
   - **多课题隔离提示**：如果与另一课题的队友不同，请对第二课题单独指定 `--repo-name yao-research-<课题名> --repo-dir ~/.yao-research/repo-<课题名>` 分开建库。
   - 若确实希望 AI 在**本课题目录**内记住手动流程提醒，可显式传 `--mode project-rule`（同时维护目录内的 `AGENTS.md` 与 `CLAUDE.md`，绝不触碰全局规则）。

3. **标准日常上传与周一双轨周报（19:00 截稿）**：
   ```bash
   # 1) 只同步 Git 已跟踪源码（加 --delete 可同步清理远端已删文件）
   python3 ~/.yao-research/hub/scripts/yao_sync.py sync-code --from-dir "<当前科研目录>" --source .

   # 2) 显式附加指定结果 CSV / PNG / PDF 到本周周报 artifacts/
   python3 ~/.yao-research/hub/scripts/yao_sync.py attach --from-dir "<当前科研目录>" results/metrics.csv figures/eval.png

   # 3) 预览清单与完整未截断正文（如需一并看代码全文加 --full-code），获得 12 位确认码
   python3 ~/.yao-research/hub/scripts/yao_sync.py preview --from-dir "<当前科研目录>"

   # 4) 经本人逐字确认后，带确认码提交并推送
   python3 ~/.yao-research/hub/scripts/yao_sync.py push --confirm-hash <12位确认码> --from-dir "<当前科研目录>"
   ```

---

## 二、私有库目录结构

```text
<你的GitHub>/yao-research-memory/     # Private（协作者：个人库平台层可写，组织库可只读 pull；默认仅 taoyao2005）
├── .gitignore                        # 拦截点文件(.netrc/.env/.claude/.vscode)/密钥JSON/大模型权重/大归档
├── README.md                         # 仓库说明
├── profile.md                        # 极简成员档案（无本机环境遥测）
├── progress/
│   └── YYYY-MM-DD.md                 # 日常今日科研进展摘要
├── code/
│   └── <project>/...                 # 经 sync-code 导入的纯净源码（无点文件/CSV/PDF）
└── weekly/
    └── YYYY-Www/
        ├── meta.yaml                 # 课题、阻塞、meeting_request 等元信息
        ├── evidence.md               # AI 生成的事实证据页（≤2 页，全部相对链接，含失败尝试）
        ├── judgment.md               # 本人亲自思考确认的判断页（≤300 字）
        └── artifacts/                # 经 attach 显式附加的实验图（PNG/JPG/PDF）与指标表（CSV/JSON）
```
