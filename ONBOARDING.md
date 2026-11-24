# 备选安装指南（无 `gh` CLI / 纯网页建私库）

如果你使用本机尚未安装或无法使用 GitHub CLI（`gh`），可按以下 3 步在网页新建私有仓库，然后再让 AI 完成其余本地装配：

1. 打开 `https://github.com/new`：
   - **Repository name** 填写：`yao-research-memory`
   - **Visibility** 必须勾选 **`Private`**（严禁公开）
   - 点击 **Create repository**
2. 进入新建仓库的 **Settings → Collaborators → Add people**：
   - 添加 `taoyao2005`（导师）
   - 添加 `treerobin06`（会议秘书）
3. 克隆协议中心并初始化本地库后绑定远端：
   ```bash
   git clone https://github.com/Yao-Research-Group/research-hub.git ~/.yao-research/hub
   python3 ~/.yao-research/hub/scripts/yao_sync.py init --skip-remote
   cd ~/.yao-research/repo
   git remote add origin https://github.com/<你的GitHub账号>/yao-research-memory.git
   git push -u origin main
   ```

## 常见问题

- **我不想装到全局规则，只想管某一个科研目录怎么办？**
  对你的 AI 说“帮我把这个项目装到当前目录”或手动加参数 `python3 ~/.yao-research/hub/scripts/yao_sync.py init --project-dir /path/to/your/project`。
- **如果做科研时暂时断网或代理不稳定会报错卡住吗？**
  不会。`yao_sync.py push` 会在本地先完成安全检查和 `git commit`，推送超时（10 秒）时自动保留本地提交队列并正常返回，下次有网时自动一并补推。
- **如何取消授权或卸载？**
  随时可在私有仓库 `Settings -> Collaborators` 移除协作者，或删除全局规则文件中 `<!-- BEGIN YAO-RESEARCH-HUB -->` 到 `<!-- END YAO-RESEARCH-HUB -->` 区块并移除 `~/.yao-research/`。
