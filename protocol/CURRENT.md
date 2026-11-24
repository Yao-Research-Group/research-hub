# 课题组科研今日进展与周报手动同步协议（v2.0 隐私优先版）

> 本文件位于 `~/.yao-research/hub/protocol/CURRENT.md`。**绝不自动触发、绝不修改全局或项目 AI 规则文件、绝不静默上传。** 仅在用户主动要求整理并预览科研进展时执行。

---

## 1. 适用范围硬边界（仅限用户主动触发 + 授权目录）

- **必须由用户主动触发**：只有当用户明确要求整理今天或本周科研进展时才开始准备；平时对话收尾不做任何自动写入或推送。
- **严格目录白名单**：当前工作目录必须位于用户亲自登记在 `~/.yao-research/local-policy.json` 的科研目录内。
- **严格内容脱敏**：
  - 绝不在进展/周报中写入其他目录的项目内容、个人课程、私人事务、姓名/手机号/邮箱、或任何本机绝对路径（如 `/Users/...`、`OneDrive`、`~/Desktop`、`~/dev` 等，Git `pre-commit` 钩子会硬拦截）。
  - 绝不上传源代码、数据集、图片、PDF 或模型权重；只保留纯文本摘要（单文件 ≤ 64 KiB）。

---

## 2. 手动同步标准三步（主动整理 → 展示明文预览 → 用户确认后推送）

### 第一步：整理极简纯文本摘要

- **日常今日进展** → `~/.yao-research/repo/progress/YYYY-MM-DD.md`（仅 3–8 行）：
  - **今日做了什么**：一句话写清当前课题动作与可核对结论/数字。
  - **失败尝试或卡点**：如实记录跑不通或负结果。
  - **下一步**：下一件具体事项。
- **周报汇总（如用户要求交本周周报，常规截稿为每周一 19:00）** → `~/.yao-research/repo/weekly/<week>/report.md` 与 `meta.yaml`：
  - 基于近期 `progress/*.md` 归纳不超过 2 页的客观本周进展（严禁编造不存在的实验；若暂无可分享内容则如实说明）。

### 第二步：运行预览并将完整明文交由用户逐字 Review

```bash
python3 ~/.yao-research/hub/scripts/yao_sync.py preview --from-dir "<当前科研目录>"
```

- **必须把即将提交的全部文件明文完整展示给用户**，询问用户是否需要删改或确认上传。

### 第三步：仅在用户明确确认后才允许推送

```bash
python3 ~/.yao-research/hub/scripts/yao_sync.py push --confirm --from-dir "<当前科研目录>"
```

- 若用户不同意推送或要求修改，立即停止推送并按用户要求修改或删除草稿。

---

## 3. 查看协议更新（不静默改代码）

- 查看协议中心是否有新版规范（只读）：`python3 ~/.yao-research/hub/scripts/yao_sync.py check-update`
- 经用户同意后手动升级本地协议中心：`python3 ~/.yao-research/hub/scripts/yao_sync.py upgrade`
