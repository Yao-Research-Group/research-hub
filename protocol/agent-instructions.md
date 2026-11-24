# 课题组科研记忆与周报 Agent 运行规范（动态拉取版）

> 本文件由所有接入成员的 AI 助手在工作前自动拉取或核对版本缓存。所有要求以远端 `Yao-Research-Group/research-hub` 的最新版本为准。

## 1. 启动前协议热更新（Protocol Sync）

每次开始处理科研记录或周报任务前，先运行或等价检查：

```bash
python3 ~/.yao-research/scripts/sync_report.py --repo-dir ~/.yao-research/repo --mode pull-protocol
```

- 检查 `protocol/version.json` 的 `schema_version`、`current_week`、`current_deadline` 与规范要求，并将提示词/模板缓存到 `~/.yao-research/protocol-cache/`。
- **安全边界**：只自动缓存声明式的 Markdown/JSON 提示词、模板与时间表；**不要**自动盲目覆盖本地正在运行的 Python 脚本（脚本升级需经用户确认）。
- 开工顺便检查私有库 `reviews/` 下是否有老师批示（`*-advisor.md`）或预读反馈（`*-ai-review.md`）：若有尚未回复的问题，在回复用户时简短提醒。

## 2. 科研相关性过滤器（全局规则场景必守）

当本规范安装在**全局 Agent 规则**（如 `~/.claude/CLAUDE.md`、`~/.codex/AGENTS.md`、`~/.gemini/GEMINI.md`）时，AI 必须先判断当前对话是否属于**姚老师课题组科研/工程工作**：

- **应该记录（Trigger）**：课题组方向实验、模型训练与评测、算法实现与代码调试、论文阅读与写作、课题组产品原型、周报/组会准备、跨成员研究协作决策。
- **静默跳过（Ignore）**：个人课程作业与考试、日常闲聊、私人生活/医疗/财务、账号密码配置、未经授权的第三方保密业务数据。
- **不确定时**：不主动外传，可简短询问用户“本次工作是否需要记入课题组科研日志？”。

## 3. 无感日常沉淀（Daily & Projects）

只要本次对话产生了可验证的科研/工程进展（哪怕只有失败探索或排查结论），在收尾时静默执行以下两步，无需用户每次手动说“写日志”：

1. **追加按日流水** `daily/YYYY-MM-DD.md`（参考模板 `templates/daily-log.md`）：
   - 写清做了什么、改动文件/commit/命令、核心数字（含负结果与失败尝试）、阻塞与待定事项。
   - **禁止编造**：所有数字与路径必须来自本轮实际工具输出、日志或代码，不得脑补。
2. **覆写项目活页卡** `projects/<project-slug>.md`（参考模板 `templates/project-card.md`）：
   - 硬上限 **≤80 行**，采用**覆写制**（只保留截至今天的最新状态、论文管线阶段、停止条件、活跃路线与已证伪墓地），不把每日流水账堆进项目卡。
   - 若本轮尝试被证明无效，必须记入项目卡的“失败尝试与已证伪墓地”，防止后续重复踩坑。
3. 校验并推送到本人私有仓库：
   ```bash
   python3 ~/.yao-research/scripts/sync_report.py --repo-dir ~/.yao-research/repo --mode daily --push
   ```

## 4. 周报冻结提交规范（Weekly Freeze）

每周截止前（或用户要求整理本周周报时），在 `weekly/<YYYY-Www>/` 下准备以下文件：

1. **`evidence.md`（AI 生成，严格 ≤2 页）**：
   - 严格按 `prompts/student-evidence.md` 从 `daily/`、相关代码仓 `git log`、结果文件与笔记中归纳。
   - **只陈述事实与出处**（附文件名、commit 或链接），**必须包含失败尝试与反面结果**，**不做解释、不吹嘘、不替人下结论**。
2. **`judgment.md`（本人撰写，严格 ≤半页，建议 150–400 字）**：
   - **严禁 AI 代替学生编造判断或填充占位符！**
   - 按 `prompts/student-judgment-guide.md` 向学生提问 5 个核心问题；允许学生语音转文字或用大白话作答后由 AI 仅做文字梳理，且最终内容必须经学生本人确认后写入。
3. **`meta.yaml`**：
   - 准确填写 `week`、`student_name`、`meeting_request`（`none` / `group` / `1on1` / `all` 四选一）、`blockers_summary`、`help_needed_from`、`code_repos`。
4. **`product.md`（仅含产品任务的同学周五 21:00 前补齐）**：
   - 记录本周可演示内容与下周交付物。
5. 提交前强制跑校验推送：
   ```bash
   python3 ~/.yao-research/scripts/sync_report.py --repo-dir ~/.yao-research/repo --mode weekly --week <YYYY-Www> --push
   ```

## 5. 回复老师 AI 问题（三选一书面回复）

当 `reviews/<YYYY-Www>-ai-review.md` 或 `reviews/<YYYY-Www>-advisor.md` 出现提问时，学生在会前通过 `reviews/<YYYY-Www>-reply.md` 对每个问题做简短书面回复（三选一）：
- `已回答`：直接附答案与依据位置
- `将在 <YYYY-MM-DD> 前完成`：写明计划动作
- `不同意`：写明具体理由与依据

注：当老师的批示与老师 AI 的问题不一致时，**以老师本人批示为准**。

## 6. 代码与大文件规范

- 真实科研代码**继续留在各自原科研/产品仓库**，不要把大代码仓复制到本记忆库；只需在 `meta.yaml` 和 `evidence.md` 中写明仓库地址与关键 commit/文件路径。
- 若有关键小图表（PNG/SVG）或摘要 CSV 需要随周报附带，放进 `weekly/<YYYY-Www>/artifacts/`（单文件必须 < 2 MiB）。
- 严禁向记忆库提交 `.env`、密钥、模型权重（`.pt`/`.ckpt`/`.pth`/`.safetensors`）或大数据集（`.parquet`/`.csv` > 2MB）。
