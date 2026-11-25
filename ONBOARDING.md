# 备选安装与常用管理指南（v3.1.0 · 无 `gh` CLI 网页建库 + 常用命令）

## 一、无 `gh` CLI 时 4 步网页建私有库并完成首次推送

1. 打开 `https://github.com/new`：
   - **Repository name** 填写：`yao-research-memory`（若多课题需按队友分库隔离，可改如 `yao-research-pfn`）
   - **Visibility** 必须勾选 **`Private`**（严禁选 Public）
   - 点击 **Create repository**
2. 进入新仓库 **Settings → Collaborators → Add people**，添加导师 `taoyao2005`（如实说明：个人私有库在 GitHub 平台层实际赋予协作者 Write/Push 权限；组织库可选只读 Read）。
3. 克隆协议中心并在具体的科研课题子目录初始化（禁止 `/`、`$HOME`、`~/Documents` 等宽目录）：
   ```bash
   git clone https://github.com/Yao-Research-Group/research-hub.git ~/.yao-research/hub
   python3 ~/.yao-research/hub/scripts/yao_sync.py init --project-dir "/path/to/your/specific-topic-dir" --skip-remote
   ```
4. **先产生首次本地提交，再关联远端推送**（`init` 只建目录骨架不自动产生提交，直接 `git push` 会失败）：
   ```bash
   python3 ~/.yao-research/hub/scripts/yao_sync.py preview --from-dir "/path/to/your/specific-topic-dir"
   python3 ~/.yao-research/hub/scripts/yao_sync.py push --confirm-hash <上一步输出的12位确认码> --from-dir "/path/to/your/specific-topic-dir" --skip-remote

   cd ~/.yao-research/repo
   git remote add origin https://github.com/<你的GitHub账号>/yao-research-memory.git
   git push -u origin main
   ```

---

## 二、日常高频命令速查（v3.1.0）

- **只同步干净源码（默认仅 Git 已跟踪的明确源码扩展名，跳过 `.env`/`.netrc`/`.claude/`/CSV/PDF）**：
  ```bash
  python3 ~/.yao-research/hub/scripts/yao_sync.py sync-code --from-dir "<课题目录>" --source . --dest code/my-project
  # 若有未 git add 的新源码脚本想纳入，加 --include-untracked；若要同步删掉源端已删文件，加 --delete
  ```
- **显式附加实验图表 / CSV / JSON / PDF 到周报 `artifacts/`**：
  ```bash
  python3 ~/.yao-research/hub/scripts/yao_sync.py attach --from-dir "<课题目录>" figures/loss.png results/metrics.csv
  ```
- **预览清单、完整明文并生成确认码**（加 `--full-code` 可同时展开源码正文）：
  ```bash
  python3 ~/.yao-research/hub/scripts/yao_sync.py preview --from-dir "<课题目录>"
  ```
- **凭确认码正式推送**（默认 `git push` 超时 60s，可加 `--push-timeout 120`）：
  ```bash
  python3 ~/.yao-research/hub/scripts/yao_sync.py push --confirm-hash <12位确认码> --from-dir "<课题目录>"
  ```
- **多项目分库隔离**（不同项目队友不同，勿混用同一私有库）：
  ```bash
  python3 ~/.yao-research/hub/scripts/yao_sync.py init \
    --project-dir "/path/to/project-b" \
    --repo-name yao-research-project-b \
    --repo-dir ~/.yao-research/repo-project-b \
    --collaborators taoyao2005,teammate_b
  ```
- **导师端白名单审批与安全拉取**：
  ```bash
  python3 ~/.yao-research/hub/scripts/advisor_collect.py --allow-student <学生GitHub> --accept-invites --sync --digest
  ```
- **检查更新与彻底退出**：
  - `check-update` / `upgrade`：查看与手动升级 `~/.yao-research/hub` 并刷新本地 `pre-commit` 钩子；
  - 完全退出：在 GitHub 仓库 `Settings -> Collaborators` 移除协作者，本地删去 `~/.yao-research/` 即可。
