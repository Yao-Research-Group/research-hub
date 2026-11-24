# 备选安装与常用管理指南（v3.0 · 无 `gh` CLI 网页建库 + 常用命令）

如果本机未安装或不便使用 GitHub CLI（`gh`），可按以下 3 步手动建私有库，随后正常使用全部白名单围栏与 `attach` / `sync-code` / `upload` 功能：

## 一、3 步网页建私有库（无 `gh` CLI 时）

1. 打开 `https://github.com/new`：
   - **Repository name** 填写：`yao-research-memory`
   - **Visibility** 必须勾选 **`Private`**（严禁选 Public）
   - 点击 **Create repository**
2. 进入新仓库 **Settings → Collaborators → Add people**，添加导师为协作者：
   - `taoyao2005`（导师；组织库选 Read/Pull，个人私有库直接添加即可）
   - 若同课题组需要队友互看代码，可自行再加队友 GitHub ID。
3. 克隆协议中心并在你指定的科研目录初始化（按需二选一 `--mode`）：
   ```bash
   git clone https://github.com/Yao-Research-Group/research-hub.git ~/.yao-research/hub

   # 选项 A：仅在该科研目录启用无感规范（绝不碰全局）
   python3 ~/.yao-research/hub/scripts/yao_sync.py init \
     --project-dir "/path/to/your/research-dir" \
     --mode project-rule \
     --confirm-mode auto-in-allowlist \
     --skip-remote

   # 选项 B：零侵入纯手动工具
   # python3 ~/.yao-research/hub/scripts/yao_sync.py init --project-dir "/path/to/your/research-dir" --skip-remote

   cd ~/.yao-research/repo
   git remote add origin https://github.com/<你的GitHub账号>/yao-research-memory.git
   git push -u origin main
   ```

---

## 二、日常高频命令速查

- **附加实验图表 / CSV 到本周周报 `artifacts/`**：
  ```bash
  python3 ~/.yao-research/hub/scripts/yao_sync.py attach --from-dir "<科研目录>" figures/loss.png results/metrics.csv
  ```
- **同步当前科研项目源码树到 `code/`（自动跳过缓存/密钥/权重/.gitignore）**：
  ```bash
  python3 ~/.yao-research/hub/scripts/yao_sync.py sync-code --from-dir "<科研目录>" --source src --dest code/my-project
  ```
- **一条龙附加产物 + 同步代码 + 推送**：
  ```bash
  python3 ~/.yao-research/hub/scripts/yao_sync.py upload --from-dir "<科研目录>" --attach results/metrics.csv --sync-code . --mode weekly --confirm
  ```
- **切换确认策略 / 查看或管理本地白名单**：
  ```bash
  python3 ~/.yao-research/hub/scripts/yao_sync.py policy --show
  python3 ~/.yao-research/hub/scripts/yao_sync.py policy --set-confirm-mode auto-in-allowlist
  python3 ~/.yao-research/hub/scripts/yao_sync.py policy --remove-project-dir "<旧科研目录>"
  ```
- **添加第 2 个科研目录到白名单**：再对该目录执行一次 `init --project-dir "<第2个科研目录>"` 即可安全追加，绝不影响已有仓库数据。
- **检查或手动升级协议中心**：
  - `python3 ~/.yao-research/hub/scripts/yao_sync.py check-update`（只读查看更新）
  - `python3 ~/.yao-research/hub/scripts/yao_sync.py upgrade`（确认后升级并刷新 `.git/hooks/pre-commit` 护栏）
- **完全退出或撤销授权**：在私有库 `Settings -> Collaborators` 一键移除协作者即可；本地删除 `~/.yao-research/`（以及选用 `project-rule` 时科研目录内 `AGENTS.md` 中的标记块）即可彻底退出。
