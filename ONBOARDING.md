# 备选安装指南（无 `gh` CLI / 纯网页建私库，v2.0.1）

如果本机未安装或不便使用 GitHub CLI（`gh`），可按以下 3 步手动建私有库并启用全部本地隐私硬拦截：

1. 打开 `https://github.com/new`：
   - **Repository name** 填写：`yao-research-memory`
   - **Visibility** 必须勾选 **`Private`**（严禁选 Public）
   - 点击 **Create repository**
2. 进入新仓库 **Settings → Collaborators → Add people**，添加协作者（组织库选 **Read / Pull**；个人私有库受 GitHub 平台限制直接添加标准协作者即可，也可仅添加导师，本人随时可一键移除）：
   - `taoyao2005`（导师）
   - `treerobin06`（会议秘书，可选）
3. 克隆本仓库并绑定你亲口指定的一个科研工作目录（注意：**绝不写入任何全局或项目 AI 规则文件，也不会自动上传**）：
   ```bash
   git clone https://github.com/Yao-Research-Group/research-hub.git ~/.yao-research/hub
   python3 ~/.yao-research/hub/scripts/yao_sync.py init --project-dir "/path/to/your/research-dir" --skip-remote
   cd ~/.yao-research/repo
   git remote add origin https://github.com/<你的GitHub账号>/yao-research-memory.git
   git push -u origin main
   ```

## 日常使用与管理

- **完全手动触发**：只有当你主动对 AI 说“整理今天（或本周）的科研进展，先给我 preview 看一下”时才生成与预览，你亲自看清内容并确认后才会执行 `push --confirm`。
- **可选本地提醒**：你可以自己决定是否在系统日历、Todo 或本地工具设一个周一晚 7 点前的纯提醒（只提醒本人，不触发自动上传）。
- **添加第 2 个科研目录到白名单**：再对该目录执行一次 `init --project-dir "<第2个科研目录>"` 即可安全追加白名单，不会影响已有记录。
- **手动检查与更新协议中心**：
  - `python3 ~/.yao-research/hub/scripts/yao_sync.py check-update`（只读查看 hub 更新摘要）
  - `python3 ~/.yao-research/hub/scripts/yao_sync.py upgrade`（确认后升级本地 `~/.yao-research/hub`）
- **完全退出或撤销授权**：在私有库 `Settings -> Collaborators` 一键移除协作者即可；本地删除 `~/.yao-research/` 即可彻底清理（因本工具从未修改过你的全局或项目 AI 规则文件）。
