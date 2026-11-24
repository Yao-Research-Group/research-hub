# 3 分钟接入指南（Yao Research Group 科研记忆与周报系统）

> 核心原则：**你只管平时正常和 AI 做科研，AI 自动帮你沉淀日常记录；每周交周报时，AI 负责整理客观「证据页」，你用 5 分钟写清自己的「判断页」。**

---

## 方式一：让你的 AI 助手一键完成（推荐，支持 Claude Code / Codex / Antigravity / Cursor）

直接把下面整段话发给你的 AI 编程助手即可（无需自己敲命令）：

```text
请读取 https://raw.githubusercontent.com/Yao-Research-Group/research-hub/main/ONBOARDING.md 与 https://raw.githubusercontent.com/Yao-Research-Group/research-hub/main/protocol/agent-instructions.md ，帮我接入课题组科研记忆与周报系统并完成本周（2026-W48）周报准备与提交。

按以下步骤执行：
1. 先检查我本机 python3（>=3.9）、git 和 gh（GitHub CLI）的登录状态。如果 gh 未登录，先引导我完成一次 gh auth login（或改用文档里的网页建库方案）。
2. 只向我确认 3 个信息：
   ① 我的中文姓名、拼音缩写（用作私有库后缀）、身份（phd/master/other）、当前主课题名称与结对同学；
   ② 我是否指定一个固定的科研项目根目录：如果指定，请将工作流规则装入该目录的 AGENTS.md / CLAUDE.md；如果我不指定（或希望平时随时无感触发），请装入我的全局 Agent 规则文件（并启用科研相关性过滤，只记录课题组科研工作，绝不记录个人隐私或无关内容）；
   ③ 本周相关代码仓库路径、实验日志或笔记目录。
3. 从 Yao-Research-Group/research-hub 下载 scripts/init_student.py 与 scripts/sync_report.py 到 ~/.yao-research/scripts/，运行 init_student.py 为我创建【私有】GitHub 仓库 yao-research-<拼音> 并自动邀请 taoyao2005 和 treerobin06 作为协作者（严禁建为 public 仓库，且绝不泄露任何密钥/token）。
4. 严格按 protocol/prompts/student-evidence.md 读取我的代码提交、实验日志和笔记，生成本周 weekly/2026-W48/evidence.md（必须包含失败尝试与出处链接）。
5. 按 protocol/prompts/student-judgment-guide.md 用 5 个简短问题引导我口述或输入我本人的判断，由我确认后写入 weekly/2026-W48/judgment.md（≤半页，严禁 AI 替我编造判断）与 meta.yaml。
6. 运行 python3 ~/.yao-research/scripts/sync_report.py --mode weekly --week 2026-W48 --push 完成校验与提交，并向我汇报私有仓库地址和校验结果。
```

---

## 方式二：手动 / 无 `gh` CLI 备选（3 步网页建私库）

如果你本机还没有安装 GitHub CLI（`gh`），也可以 1 分钟手动建私库：

1. 打开 `https://github.com/new`：
   - Repository name 填：`yao-research-<你的拼音>`（例如 `yao-research-zhangming`）
   - 可见性**必须选 `Private`**（切勿选 Public）
   - 点击 **Create repository**
2. 进入新仓库的 **Settings → Collaborators → Add people**：
   - 分别添加 `taoyao2005`（姚老师）和 `treerobin06`（姜博文，秘书）
3. 克隆或运行初始化脚本跳过远程建库：
   ```bash
   mkdir -p ~/.yao-research/scripts
   curl -fsSL https://raw.githubusercontent.com/Yao-Research-Group/research-hub/main/scripts/init_student.py -o ~/.yao-research/scripts/init_student.py
   curl -fsSL https://raw.githubusercontent.com/Yao-Research-Group/research-hub/main/scripts/sync_report.py -o ~/.yao-research/scripts/sync_report.py
   python3 ~/.yao-research/scripts/init_student.py \
     --name "你的姓名" --slug "你的拼音" --role phd --topic "你的主课题" \
     --skip-remote --repo-dir ~/.yao-research/repo
   cd ~/.yao-research/repo
   git remote add origin https://github.com/<你的GitHub账号>/yao-research-<你的拼音>.git
   git push -u origin main
   ```

> 若完全不会用命令行，也可以在 GitHub 网页私有仓库里点击 **Add file → Create new file / Upload files**，按模板新建 `profile.md` 和 `weekly/2026-W48/{meta.yaml, evidence.md, judgment.md}`。

---

## 本周及后续时间节奏

| 时间节点 | 事项 | 说明 |
|---|---|---|
| **本周试跑（2026-11-24，周二）** | **今天 16:00 前**提交 `2026-W48` 周报；**19:00** 全员会（地点/链接见群通知） | 下周起周二改为路由时段，不再全员干坐 |
| **常规周节奏 · 周一 18:00 前** | 提交本周周报（`evidence.md` + `judgment.md` + `meta.yaml`） | 缺 `judgment.md` 视为未提交 |
| **周二 19:00 前 / 19:00** | 深入评审课题交预读材料 / 周二路由时段（1:1 或小组） | 仅被排到的同学参加 |
| **周四 19:00** | 深度研究评审（每周聚焦 2 个课题，提前 24h 冻结） | 会上只做深度讨论，不念常规进度 |
| **周五 21:00 前 / 周六 19:00/21:00** | 更新 `product.md` / 产品评审（按通知时间） | 演示可运行产物与下周交付物 |

---

## 隐私与安全红线

- **仓库必须保持 Private**：同步脚本每次推送前都会自动检查仓库是否为私有；一旦发现变为 Public 会立即拒绝上传。
- **自动拦截密钥与大文件**：脚本默认拦截私钥、`ghp_` / `sk-` 等 API Token、`.env`、带认证参数 URL 以及超过 2 MiB 的文件，且 `.gitignore` 默认屏蔽 `.pt/.ckpt/.pth/.parquet` 等权重与数据文件。
- **全局接入时的内容过滤**：如果将规则装进全局 `AGENTS.md` / `CLAUDE.md`，AI 只在涉及**课题组科研、实验、论文、代码或产品任务**时自动记录到 `daily/`，遇到个人课程、日常私事、非课题组项目时静默跳过。
