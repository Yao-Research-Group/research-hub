# 隐私、最小权限与程序层拦截说明（v2.0.1 · 纯手动版）

本系统采用 **「公共只读协议库 + 每人独立私有库（仅记录今日/本周科研进展摘要）」** 架构。**绝不自动触发、绝不改全局或项目 AI 规则、绝不静默上传**；一切同步完全由同学本人主动触发、先看明文再确认上传，并由以下程序层防线硬保底。

---

## 一、六项核心隐私硬防线

1. **零自动规则注入（不碰全局，也不改项目 `AGENTS.md`/`CLAUDE.md`）**：
   - 初始化时**绝不写入** `~/.claude/CLAUDE.md`、`~/.codex/AGENTS.md`、`~/.gemini/GEMINI.md`、`~/.cursor/rules/`，也**不向项目目录写入自动收尾触发规则**。
   - 平时所有对话 100% 不自动记录、不自动推送，彻底杜绝 AI 误判把非本课题对话带出去的可能。
   - 同学每次安装须明确指定一个科研工作目录（`--project-dir`）加入**纯本地不上传**的 `~/.yao-research/local-policy.json`（权限 `0600`）；在任何非白名单目录运行 `preview` 或 `push` 会被程序直接拦截（退出码 `5`）。

2. **最小数据化：只传「今日进展 / 本周进展」纯文本摘要**：
   - 私有库路径白名单严格限定为：
     - `README.md`、`.gitignore`、`profile.md`
     - `progress/YYYY-MM-DD.md`（今日进展摘要）
     - `weekly/YYYY-Www/meta.yaml`、`weekly/YYYY-Www/report.md`（及向后兼容历史 `evidence.md`、`judgment.md`）
   - **拒收任何源码（`.py`/`.js`/`.ipynb`）、数据表（`.csv`/`.xlsx`/`.parquet`）、图片、PDF、压缩包、模型权重或未列明目录文件**；单文件硬上限为 **64 KiB**。
   - **零本机环境遥测**：不上传主机名、操作系统版本、本机邮箱、工作目录绝对路径或 AI 工具列表；`profile.md` 仅留 GitHub ID（及本人选填的称呼/课题名）。

3. **程序层双保险：原生 Git `pre-commit` 硬钩子**：
   - 初始化时在 `~/.yao-research/repo/.git/hooks/pre-commit` 自动安装纯 Python3 硬钩子。每次提交只审计本次暂存的新增/修改文件，即使绕过 `yao_sync.py` 直接 `git commit` 也会拦截：
     - 非白名单文件路径；
     - 单文件大于 `64 KiB`；
     - **外部本机路径扫描**：拦截正文中的 `/Users/...`、`/home/...`、`/Volumes/...`、`C:\Users\...`、`~/OneDrive`、`~/Desktop`、`~/Documents`、`~/Downloads`、`~/dev`、`~/Projects`、`OneDrive` 等本机路径；
     - **密钥与个人身份信息（PII）扫描**：拦截私钥、GitHub/OpenAI/Anthropic/Bearer Token、带认证参数 URL、非 `noreply` 邮箱地址及 11 位中国大陆手机号。

4. **完全手动触发：「主动整理 → 预览明文 → 确认后推」**：
   - 仅当同学自己主动让 AI 整理当前目录的今日或本周进展时，才写入草稿并运行 `yao_sync.py preview`。
   - `preview` 会将**本次即将上传的完整明文**展示给用户逐字审阅；`yao_sync.py push` 强制要求 `--confirm` 参数（未传则返回退出码 `6`），必须由同学明确点头后才提交推送。
   - 同学可自行决定是否设置一个**纯本地提醒**（如系统日历或本地日程只提醒本人周一晚 7 点前手动整理周报，绝不自动执行上传）。

5. **协作者最小授权与 GitHub 平台权限边界**：
   - 支持 `--collaborators` 显式指定协作者（例如 `--collaborators taoyao2005` 仅邀请导师；默认从 `protocol/version.json` 读取 `taoyao2005` 与 `treerobin06`）。
   - GitHub **组织库**自动限制为 `pull`（只读）；**GitHub 个人私有库**因平台接口限制仅支持标准协作者权限（`permission=pull` 会返回 HTTP 422，`yao_sync.py` 已做自动兼容回退，汇总脚本 `secretary_collect.py` 仅执行只读 `git pull`），本人可随时在 GitHub 仓库 `Settings -> Collaborators` 一键撤销授权。

6. **绝不后台静默拉取并覆盖可执行脚本**：
   - 绝不在后台自动 `git pull` 覆盖 `.py` 脚本。查看更新需显式运行 `yao_sync.py check-update`（只读查看更新内容）；仅当本人确认后再运行 `yao_sync.py upgrade`。

---

## 二、推荐叠加：宿主 AI 工具的推送权限锁（双保险）

除脚本强制要求 `--confirm` 外，建议同时在所用 AI 工具里将推送命令设为“每次弹窗询问”，形成不依赖 AI 自觉的宿主级硬锁：

### Claude Code（`.claude/settings.local.json` 或 `~/.claude/settings.json`）
```json
{
  "permissions": {
    "ask": [
      "Bash(python3 *yao_sync.py push*)",
      "Bash(git -C ~/.yao-research/repo push*)"
    ]
  }
}
```

---

## 三、诚实边界说明

- **能防住什么**：彻底杜绝静默自动触发、杜绝篡改全局或项目规则、杜绝非授权目录触发同步、杜绝源码/数据/大文件/密钥/带本机路径片段进入私有库、杜绝未经本人审阅明文的任何后台推送。
- **技术上不能单靠正则识别什么**：如果用户在白名单项目里用不带路径的纯文字转述了敏感内容，正则无法凭语义分辨——因此本系统强制采用**“仅主动手动触发 + 每次推送前向本人展示仅几行的完整明文进展 + 本人确认后才推”**作为最终防线，并且仓库保持 `Private`（仅本人、导师与秘书只读可见）。
