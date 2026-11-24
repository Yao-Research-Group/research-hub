# 隐私、目录围栏与分层护栏说明（v3.0 · 强白名单可审计版）

v3.0 的设计原则是 **「硬围栏死锁在目录层级，内部科研资产放开审计，Tier-1 拦危险品、Tier-2 做质量提醒」**——既避免“全局自动偷传私人聊天”，也解决此前“卡死源码/图片/结果表导致全员写本机死链 `E:/...`、导师无法核查原始数据”的痛点。

---

## 一、七项安全与隐私硬边界

1. **绝对零全局规则入侵（Zero Global Injection）**：
   - 无论是 `--mode manual`（默认）还是 `--mode project-rule`，本工具**绝不读取、写入或追加** `~/.claude/CLAUDE.md`、`~/.codex/AGENTS.md`、`~/.gemini/GEMINI.md`、`~/.cursor/rules/` 等任何用户全局 AI 配置。
   - 若选择 `--mode project-rule`，只会在**用户亲口指定的那 1 个白名单科研工作目录**内维护受控标记块 `<!-- BEGIN YAO-RESEARCH-HUB --> ... <!-- END YAO-RESEARCH-HUB -->`（`<project_dir>/AGENTS.md`）。台电脑上其他目录、课程、公司项目与私人对话完全不受影响。

2. **目录围栏（Hard Directory Jail）+ 防软链/路径穿越**：
   - 授权目录列表严格保存在纯本地 `~/.yao-research/local-policy.json`（文件权限 `0600`，**永不提交到任何 Git 仓库**）。
   - 每次执行 `attach`、`sync-code`、`upload`、`preview`、`push`，程序都会对来源工作目录以及**每一个待复制源文件的真实解析路径（`Path.resolve()`）**做严格公共前缀校验，同时明确拒绝符号链接（`is_symlink()`）与 `..` 越界——任何越出白名单目录的文件都会触发硬拦截（退出码 `5`）。

3. **默认仅授权导师 `taoyao2005`（彻底移除默认第三方/秘书账号）**：
   - 默认协作者仅 `[{"github": "taoyao2005", "role": "advisor"}]`。同学如有同课题组队互审需求，可自主运行 `init --collaborators taoyao2005,<teammate>` 添加队友，否则不向任何第三人开放仓库。
   - **GitHub 平台权限诚实说明**：GitHub **组织仓库**支持 API 强制设置只读 `pull`；**个人账号私有仓库**因 GitHub 平台机制限制，`-f permission=pull` 会返回 HTTP `422 Cannot assign ... permission of read`。v3.0 自动识别该 422 响应并回退为标准协作者邀请，导师端 `advisor_collect.py` 仅执行只读 `git clone`/`git pull`，同学随时可在 GitHub 仓库 `Settings -> Collaborators` 一键移除任何协作者。

4. **全面放开可审计科研资产，不误杀代码与实验图**：
   - **仓库允许路径**：`README.md`、`.gitignore`、`profile.md`、`progress/YYYY-MM-DD.md`、`weekly/YYYY-Www/{meta.yaml,evidence.md,judgment.md,report.md,artifacts/<...>}`、`code/<...>`、`sessions/YYYY-MM-DD*.md`、`projects/*.md`。
   - **单文件上限**：放宽至 **10 MiB**，常规实验图表（`.png`/`.jpg`/`.webp`/`.gif`/`.pdf`）、实验结果表（`.csv`/`.tsv`/`.json`）及源代码均可在白名单内畅通上传。
   - **无环境遥测**：依然不收集、不写入主机名、系统版本、本机邮箱或工作目录绝对路径到 `profile.md` 或 `meta.yaml`。

5. **Tier-1 硬拦截（`audit_paths` 与 `.git/hooks/pre-commit` 双重生效）**：
   - 即使绕过 `yao_sync.py` 直接 `git commit`，原生 `.git/hooks/pre-commit` 钩子也会硬性阻止以下内容进入提交历史：
     - 仓库白名单之外的路径；
     - 任何符号链接；
     - 敏感文件名（`.env`、`.env.*`、`*.pem`、`*.key`、`*.p12`、`*.pfx`、`id_rsa*`、`*cookie*`、`*credential*`、`*secret*`、`all_keys.json`、`local-policy.json`）；
     - 禁用大二进制、模型权重与环境缓存（`.pt`/`.pth`/`.ckpt`/`.safetensors`/`.onnx`/`.gguf`/`.h5`/`.hdf5`/`.parquet`/`.feather`/`.zip`/`.tar`/`.gz`/`.7z`/`.mp4`/`.db`/`.sqlite`，以及 `.venv`、`node_modules`、`__pycache__`、`wandb`、`mlruns`）；
     - 超过 `10 MiB` 的单个文件；
     - 非允许图片/PDF 的未知二进制文件或含空字节文件；
     - 所有可读文本/代码行内匹配到的私钥头、GitHub Token（`gh*_*` / `github_pat_*`）、`sk-*` Key、`Bearer` Token 与带认证查询参数的签名 URL。

6. **Tier-2 软审计提醒（输出 `[WARN]`，不阻塞提交）**：
   - 在 Markdown 汇报文件（`progress/*.md`、`weekly/*/{evidence,judgment,report}.md`、`sessions/*.md`）中自动检查并发出 `[WARN]` 提醒：
     - 正文出现 `/Users/...`、`E:/...`、`OneDrive` 等本机绝对路径死链（提醒用 `attach`/`sync-code` 上传后改相对路径）；
     - 正文遗留 AI 对学生的提示语（如“本地审阅草稿”“先生成 diff 并由用户确认”“请确认是否上传”）；
     - 汇报正文出现非 `noreply` 邮箱或 11 位中国大陆手机号（提醒本人确认是否有意保留）；
     - `evidence.md` 超 `6,000` 字符或 `judgment.md` 超 `800` 字符 / 暂缺时提醒保持“≤2 页证据 + ≤300 字判断”。

7. **`judgment.md` 本人确认红线 + 禁止静默覆盖脚本**：
   - 如果采用 `--confirm-mode auto-in-allowlist`，白名单目录内日常进展、代码与 `artifacts/` 可无感自动提交推送；但**一旦改动涉及学生本人判断页 `weekly/*/judgment.md`**，代码层仍强制拦截（退出码 `6`）并要求显式加 `--confirm` 确认。
   - 查看或升级协议中心代码需显式执行 `check-update` / `upgrade`，绝不在后台静默 `git pull` 篡改本地脚本。
