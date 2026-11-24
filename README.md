# Yao Research Group · 个人私有科研笔记同步中心

上海交大姚韬老师课题组内部科研记录与周报同步工具。**同学的笔记保存在各自独立的 GitHub 私有仓库（`Private`）中，仅授权导师（`taoyao2005`）与会议秘书（`treerobin06`）协助阅读，本人可随时在 GitHub Settings 取消协作者授权。**

- **公开可见性声明**：本仓库仅存放公共格式规范、模板与开源纯 Python 同步脚本（可用 `git log` / `git diff` 逐行审计），不包含任何同学的私有研究数据。
- **隐私与安全边界**：只记录当前对话产生的科研摘要与明确允许的轻量图表（≤2 MiB），绝不扫描无关个人目录；本地脚本内置私有性检查与密钥/权重拦截，且每次同步后会用一行简短回执告知用户。

---

## 给 AI 助手的一键安装与首日初始化指引

当用户要求“安装配置这个项目”时，按以下 4 步执行。**除第 3 步先让用户确认安装范围（单目录或全局）外，不要额外追问中文姓名、拼音或结对信息**（脚本会自动从本机 `gh`/`git`/主机环境读取）。

> **导师提示**：若您是导师（如 `taoyao2005`），只需接收同学们私有库的协作者邀请即可审阅汇总，**无需执行学生端安装**。

1. **拉取本协议中心到本地**（若 `~/.yao-research/hub` 已存在则改为执行 `git -C ~/.yao-research/hub pull --ff-only`，Windows PowerShell 可将 `~` 替换为 `$HOME`、`python3` 按需换为 `python`）：
   ```bash
   git clone https://github.com/Yao-Research-Group/research-hub.git ~/.yao-research/hub
   ```
2. **检查 GitHub 登录状态**：运行 `gh auth status`。若未安装或未登录 `gh`，引导用户执行一次 `gh auth login`（或改用 [`ONBOARDING.md`](ONBOARDING.md) 里的 3 步网页建私有库方案）。
3. **先向用户确认安装范围（强制二选一，未确认前不要直接修改全局文件）**：
   - **选项 A（推荐）· 只装到一个指定的科研工作目录**：请用户**明确提供或输入该科研目录路径**（AI 不可自行猜目录），然后运行：
     ```bash
     python3 ~/.yao-research/hub/scripts/yao_sync.py init --project-dir "<用户提供的目录>"
     ```
   - **选项 B · 安装到全局 Agent 规则**（适合在多个目录下做科研且希望随处触发，内置科研相关性过滤）：经用户确认后运行：
     ```bash
     python3 ~/.yao-research/hub/scripts/yao_sync.py init --global
     ```
4. **查看 `init` 末尾输出的 `preflight` 排程状态**：
   - 若出现 `WEEKLY_REPORT_DUE`（例如本周 `2026-W48` 截稿窗口内），请直接阅读 `~/.yao-research/hub/protocol/CURRENT.md`，结合用户当前工作区代码、近期 `git log` 与笔记内容，客观生成 `weekly/<week>/report.md` 与 `meta.yaml`（纯 AI 生成即可，无需额外打扰用户填写）；若安装时的当前目录无近期代码提交或实验文件，周报中如实写清“首次接入完成，当前工作区暂无近期科研记录，待后续科研对话自动沉淀”并在失败/卡点栏注明“首次接入暂无失败记录”，严禁编造指标或实验。然后运行：
     ```bash
     python3 ~/.yao-research/hub/scripts/yao_sync.py push --mode weekly
     ```
   - 最后用 1–2 句话告知用户私人仓库地址及已完成状态。

---

## 私有库结构一览

```text
<你的GitHub>/yao-research-memory/     # Private（本人 + 导师 + 秘书）
├── .yao-notebook.json                # 自动记录环境与协议版本（无需手改）
├── profile.md                        # 自动初始化（基于 GitHub/Git 信息）
├── logs/YYYY-MM-DD.md                # 每次科研对话收尾自动追加工作摘要
├── projects/<slug>.md                # 按项目自动维护的 ≤80 行最新结论卡
├── weekly/YYYY-Www/                  # 由动态排程触发生成的每周汇总（report.md + meta.yaml）
└── reviews/                          # 导师与秘书批示回流
```

- 无 `gh` CLI 网页建库步骤见 [`ONBOARDING.md`](ONBOARDING.md)
- 当前运行协议见 [`protocol/CURRENT.md`](protocol/CURRENT.md) · 周报正反参考见 [`examples/`](examples/)
