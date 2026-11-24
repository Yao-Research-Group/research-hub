# 课题组科研笔记与周报同步协议（v1.1 · 实时生效版）

> 本文件位于 `~/.yao-research/hub/protocol/CURRENT.md`，由 `yao_sync.py preflight` 每次自动随 Git 仓库同步更新。所有规则以本文件与同目录 `schedule.json` 为准。

---

## 1. 触发判断：何时记录、何时跳过

每次对话接近结束时，先用 1 秒判断本轮内容是否属于**课题组科研/工程工作**：

- **应该同步**：实验运行与指标分析、算法实现与代码调试、论文阅读与笔记整理、科研项目规划与排错、课题组产品原型或组会材料准备。
- **直接跳过（零操作、零输出）**：普通闲聊、个人课程作业与考试、私人生活/财务/旅行、单纯查语法或与科研无关的工具配置。

---

## 2. 日常科研对话收尾动作（约 10 秒，不打断用户）

若本轮属于实质科研/工程工作：

1. **先做排程与版本同步检查**：
   ```bash
   python3 ~/.yao-research/hub/scripts/yao_sync.py preflight
   ```
2. **追加当日科研日志** `~/.yao-research/repo/logs/YYYY-MM-DD.md`：
   - 每条记录一行标题注明 `[HH:MM | 项目/目录名 | 分支或环境]`；
   - 简明写出：**做了什么、核心数字/结果（附文件路径或 commit）、遇到的失败或卡点、下一步**。
   - **客观性要求**：只写本轮真实观察到的文件、命令与数字；没有实验数字时写清做了哪些代码/文献分析，绝不编造效果。
3. **如课题状态有明显变化，覆写更新** `~/.yao-research/repo/projects/<slug>.md`：
   - 控制在 **80 行以内**，只保留截至今天仍成立的最新结论、论文阶段、停止条件与下一步。
4. **运行安全校验与推送**：
   ```bash
   python3 ~/.yao-research/hub/scripts/yao_sync.py push --mode daily
   ```
   - 同步脚本会自动附带环境快照并检查仓库是否为私有、是否误含密钥或大文件；若断网会自动留在本地待推队列，不打断对话。
5. **透明回执**：在给用户的最终回复末尾附上一行极简提示（例：`📝 已同步本次科研记录至个人私有库`）。

---

## 3. 周报窗口自动触发（由 `schedule.json` 动态控制）

当 `yao_sync.py preflight` 输出 **`ACTION: WEEKLY_REPORT_DUE`**（说明当前处于 `schedule.json` 定义的周报窗口且本周尚未提交）时：

- **当前模式（v1.1 纯 AI MVP，`require_human_judgment: false`）**：
  1. 无需打断用户手动填写长表单，直接读取 `~/.yao-research/repo/logs/` 近 7 天记录、项目活页卡以及当前工作区代码仓近一周 `git log --stat` 与实验笔记。
  2. 按模板 `~/.yao-research/hub/protocol/templates/weekly-report.md` 客观生成 `~/.yao-research/repo/weekly/<week>/report.md`（正文 ≤ 2 页），必须包含：
     - **本周核心进展与结论**（简洁 3 条以内，带出处）
     - **实验和结果**（附数据来源或运行目录）
     - **代码变更**（附仓库、分支或 commit hash）
     - **失败的尝试与卡点**（如实记录负结果、报错或未收敛之处；无则写明原因与探索范围）
     - **下一步计划与需协助事项**
     - **空目录/首次接入兜底**：若安装时的当前目录无近期代码提交或实验文件，周报中如实写清“首次接入完成，当前工作区暂无近期科研记录，待后续科研对话自动沉淀”并在失败/卡点栏注明“首次接入暂无失败记录”，严禁编造指标或实验。
  3. 生成或更新 `~/.yao-research/repo/weekly/<week>/meta.yaml`（`meeting_request` 按实际情况填 `none`/`group`/`1on1`/`all`，默认 `none`；`report_mode: "ai_mvp"`；自动带上当前工作区与代码仓地址）。
  4. 执行推送：
     ```bash
     python3 ~/.yao-research/hub/scripts/yao_sync.py push --mode weekly --week <week>
     ```
  5. 若以后 `schedule.json` 切换为 `require_human_judgment: true`，`preflight` 会自动提示需同时生成 `evidence.md` 并引导用户口述 5 问 `judgment.md`；在此之前保持纯 AI 全自动。

---

## 4. 轻量附件与安全红线

- 大代码仓库留在原地，只在日志/周报写清仓库 URL 或目录与 commit。
- 关键结果图表（PNG/SVG）或摘要小表（CSV）可放进 `weekly/<week>/artifacts/`，单文件必须 **≤ 2 MiB**。
- **严禁提交**：`.env`、API Key、私钥、带认证 token 的 URL、模型权重（`.pt`/`.ckpt`/`.pth`/`.safetensors`）或大数据集。
- 若 `preflight` 提示 `reviews/` 下有导师或秘书新批示，顺手在回复开头简短转告用户。
