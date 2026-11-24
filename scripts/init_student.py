#!/usr/bin/env python3
"""Initialize a private per-student Yao Research Group memory repo and agent rules.

Zero third-party dependencies (Python 3.9+ stdlib only).
All file I/O explicitly specifies UTF-8 encoding for cross-platform compatibility.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
import urllib.request

DEFAULT_HUB_REPO = "Yao-Research-Group/research-hub"
DEFAULT_HUB_BRANCH = "main"
BEGIN_MARKER = "<!-- BEGIN YAO-RESEARCH-SYNC -->"
END_MARKER = "<!-- END YAO-RESEARCH-SYNC -->"

STUDENT_GITIGNORE = """# Secrets & local configs
.env
.env.*
*.key
*.pem
*.p12
*.pfx
secrets/
*.local.json

# Large model weights & datasets (keep in separate experiment storage)
*.pt
*.pth
*.ckpt
*.safetensors
*.onnx
*.bin
*.parquet
*.feather
*.h5
*.hdf5
*.tar.gz
*.zip

# OS & tool caches
.DS_Store
Thumbs.db
__pycache__/
*.pyc
.venv/
.yao-cache/
"""


def current_iso_week() -> str:
    today = date.today()
    iso = today.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def sanitize_slug(raw: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", raw.strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-_")
    if not slug:
        raise ValueError(f"Invalid slug '{raw}': must contain ASCII letters or digits.")
    return slug


def run_cmd(
    cmd: List[str],
    cwd: Optional[Path] = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if check and proc.returncode != 0:
        msg = proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}"
        raise RuntimeError(f"Command failed ({' '.join(cmd)}): {msg}")
    return proc


def load_hub_text(
    rel_path: str,
    hub_dir: Optional[Path],
    hub_repo: str,
    hub_branch: str,
) -> str:
    if hub_dir is not None:
        local_file = hub_dir / rel_path
        return local_file.read_text(encoding="utf-8")
    url = f"https://raw.githubusercontent.com/{hub_repo}/{hub_branch}/{rel_path}"
    req = urllib.request.Request(url, headers={"User-Agent": "yao-research-hub/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8")


def load_version_manifest(
    hub_dir: Optional[Path],
    hub_repo: str,
    hub_branch: str,
) -> Dict[str, Any]:
    try:
        raw = load_hub_text("protocol/version.json", hub_dir, hub_repo, hub_branch)
        return json.loads(raw)
    except Exception:
        return {
            "schema_version": "1.0.0",
            "hub_repo": hub_repo,
            "hub_branch": hub_branch,
            "repo_prefix": "yao-research-",
            "current_week": current_iso_week(),
            "required_collaborators": [
                {"github": "taoyao2005", "role": "advisor", "permission": "push"},
                {"github": "treerobin06", "role": "secretary", "permission": "push"},
            ],
        }


def check_gh_ready() -> Tuple[bool, str]:
    if not shutil.which("gh"):
        return False, "GitHub CLI (gh) 未安装。"
    auth = run_cmd(["gh", "auth", "status"], check=False)
    if auth.returncode != 0:
        return False, "GitHub CLI (gh) 尚未登录（请先执行 gh auth login）。"
    user_proc = run_cmd(["gh", "api", "user", "--jq", ".login"], check=False)
    if user_proc.returncode != 0 or not user_proc.stdout.strip():
        return False, f"无法获取当前 GitHub 登录账号：{user_proc.stderr.strip()}"
    return True, user_proc.stdout.strip()


def print_manual_github_guide(slug: str, repo_prefix: str, repo_dir: Path, collabs: List[Dict[str, str]]) -> None:
    repo_name = f"{repo_prefix}{slug}"
    collab_names = ", ".join(c["github"] for c in collabs)
    print(
        "\n[需要手动建私库或先登录 gh]\n"
        "请按以下 3 步在 GitHub 网页建私有库（只需 1 分钟）：\n"
        f"  1) 打开 https://github.com/new ，仓库名填 {repo_name} ，可见性必须选【Private】；\n"
        f"  2) 进入仓库 Settings -> Collaborators -> Add people，添加：{collab_names}；\n"
        "  3) 若本地骨架未生成，先加 --skip-remote 跑本脚本，再在仓库目录执行：\n"
        f"     cd {repo_dir}\n"
        f"     git remote add origin https://github.com/<你的GitHub账号>/{repo_name}.git\n"
        "     git push -u origin main\n",
        file=sys.stderr,
    )


def ensure_private_remote_repo(
    owner: str,
    repo_name: str,
    student_name: str,
    collaborators: List[Dict[str, str]],
    dry_run: bool = False,
) -> str:
    full_name = f"{owner}/{repo_name}"
    if dry_run:
        print(f"[dry-run] Would verify/create private repo {full_name} and invite collaborators.")
        return full_name

    probe = run_cmd(["gh", "api", f"repos/{full_name}"], check=False)
    if probe.returncode == 0:
        meta = json.loads(probe.stdout)
        if not meta.get("private", False):
            raise RuntimeError(
                f"安全拦截：远端仓库 {full_name} 当前为 PUBLIC（公开）！"
                "严禁向公开仓库写入任何个人科研周报或日志，请删除或转为 Private 后重试。"
            )
        print(f"[ok] 复用已有私有仓库：{full_name}")
    else:
        desc = f"Yao Research Group 个人科研记忆与周报私有库（{student_name}）"
        run_cmd(["gh", "repo", "create", full_name, "--private", "--description", desc])
        verify = json.loads(run_cmd(["gh", "api", f"repos/{full_name}"]).stdout)
        if not verify.get("private", False):
            raise RuntimeError(f"安全拦截：新创建的仓库 {full_name} 未处于 Private 状态！")
        print(f"[ok] 已创建私有仓库：{full_name}")

    for item in collaborators:
        collab = item["github"]
        perm = item.get("permission", "push")
        if collab.lower() == owner.lower():
            print(f"[skip] 协作者 {collab} 即为仓库所有者，跳过自邀请。")
            continue
        proc = run_cmd(
            ["gh", "api", "-X", "PUT", f"repos/{full_name}/collaborators/{collab}", "-f", f"permission={perm}"],
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"邀请协作者 {collab} 失败：{proc.stderr.strip() or proc.stdout.strip()}")
        print(f"[ok] 已授权协作者：{collab} ({perm})")
    return full_name


def write_if_missing(path: Path, content: str, dry_run: bool) -> bool:
    if path.exists():
        return False
    if dry_run:
        print(f"[dry-run] Would create {path}")
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def init_local_repo(
    repo_dir: Path,
    owner: str,
    full_repo_name: str,
    student_name: str,
    slug: str,
    role: str,
    topic: str,
    partner: str,
    code_repos: str,
    has_product: str,
    week: str,
    hub_repo: str,
    hub_branch: str,
    skip_remote: bool,
    dry_run: bool,
) -> None:
    if not dry_run:
        repo_dir.mkdir(parents=True, exist_ok=True)
        if not (repo_dir / ".git").exists():
            run_cmd(["git", "init", "-b", "main"], cwd=repo_dir)
        # Ensure user.name/email exist locally if global git config is unset
        name_check = run_cmd(["git", "config", "user.name"], cwd=repo_dir, check=False)
        if not name_check.stdout.strip():
            run_cmd(["git", "config", "user.name", student_name], cwd=repo_dir)
        email_check = run_cmd(["git", "config", "user.email"], cwd=repo_dir, check=False)
        if not email_check.stdout.strip():
            run_cmd(["git", "config", "user.email", f"{slug}@users.noreply.github.com"], cwd=repo_dir)

    write_if_missing(repo_dir / ".gitignore", STUDENT_GITIGNORE, dry_run)
    write_if_missing(
        repo_dir / ".yao-hub.json",
        json.dumps({"hub_repo": hub_repo, "hub_branch": hub_branch}, ensure_ascii=False, indent=2) + "\n",
        dry_run,
    )
    write_if_missing(
        repo_dir / "README.md",
        (
            f"# {student_name} · 个人科研记忆与周报仓库\n\n"
            f"- 所属协议中心：`{hub_repo}`\n"
            "- 可见性：Private（仅本人、导师及会议秘书可见）\n"
            "- 日常流水：`daily/YYYY-MM-DD.md`\n"
            "- 项目活页卡：`projects/<slug>.md`（≤80行覆写制）\n"
            "- 每周交付：`weekly/<YYYY-Www>/{meta.yaml, evidence.md, judgment.md}`\n"
            "- 导师与 AI 批示回流：`reviews/`\n"
        ),
        dry_run,
    )
    write_if_missing(
        repo_dir / "profile.md",
        (
            "# 成员档案\n\n"
            f"- **姓名**：{student_name}\n"
            f"- **GitHub ID**：{owner}\n"
            f"- **仓库地址**：{full_repo_name}\n"
            f"- **身份**：{role}\n"
            f"- **主课题 / 研究方向**：{topic}\n"
            f"- **课题组结对成员**：{partner}\n"
            f"- **关联代码仓库**：{code_repos or '待补充'}\n"
            f"- **每周产品任务**：{has_product}\n"
        ),
        dry_run,
    )

    topic_slug = sanitize_slug(slug if not re.search(r"[a-zA-Z0-9]", topic) else topic[:32])
    projects_dir = repo_dir / "projects"
    if dry_run:
        existing_cards = list(projects_dir.glob("*.md")) if projects_dir.exists() else []
    else:
        projects_dir.mkdir(parents=True, exist_ok=True)
        existing_cards = list(projects_dir.glob("*.md"))
    if not existing_cards:
        write_if_missing(
            projects_dir / f"{topic_slug}.md",
            (
                f"# 项目活页卡：{topic}\n\n"
                "> 覆写制规则：硬上限 ≤80 行，只写截至今天仍成立的最新结论、数字、卡点与下一步。\n\n"
                f"- **更新日期**：{date.today().isoformat()}\n"
                f"- **负责人与协作者**：{student_name}（主责）；{partner}\n"
                "- **目标会议 / 截稿日**：待定\n"
                "- **当前管线阶段**：想法\n"
                "- **停止条件（Kill Criteria）**：待补充明确停损阈值\n\n"
                "## 1. 问题定义与 PRD 核心\n\n"
                "- **研究 / 产品目标**：待补充\n"
                "- **共同评测协议与强基线**：待补充\n\n"
                "## 2. 最新结论与真实指标快照\n\n"
                "- **目前最好结果**：待补充\n\n"
                "## 3. 当前活跃路线与下一步（WIP ≤ 3）\n\n"
                "1. `[init]` 梳理本周代码与实验记录，生成首期证据页与判断页。\n\n"
                "## 4. 失败尝试与已证伪墓地\n\n"
                "- 暂无记录\n"
            ),
            dry_run,
        )

    for sub in ("daily", "reviews"):
        write_if_missing(repo_dir / sub / ".gitkeep", "", dry_run)

    week_dir = repo_dir / "weekly" / week
    write_if_missing(
        week_dir / "meta.yaml",
        (
            f'week: "{week}"\n'
            f'student_name: "{student_name}"\n'
            f'student_id: "{slug}"\n'
            f'role: "{role}"\n'
            f'topic: "{topic}"\n'
            f'group_partner: "{partner}"\n'
            'meeting_request: "none"\n'
            'blockers_summary: "无"\n'
            'help_needed_from: "无"\n'
            'help_needed_what: "无"\n'
            f'has_product_task: "{has_product}"\n'
            f'code_repos: "{code_repos}"\n'
            'submitted_at: ""\n'
        ),
        dry_run,
    )
    write_if_missing(
        week_dir / "evidence.md",
        (
            "<!-- GENERATED_BY: AI -->\n"
            f"# 本周证据页（{week}）\n\n"
            f"- **提交人**：{student_name}｜**课题**：{topic}\n\n"
            "## 1. 实验和结果\n- <请由 AI 从实际日志与实验产出整理，附来源>\n\n"
            "## 2. 代码变更\n- <请附仓库、分支与 commit hash>\n\n"
            "## 3. 文献\n- <本周精读论文与对照要点>\n\n"
            "## 4. 失败的尝试\n- <必填：本周未成功、负提升或被证伪的做法>\n\n"
            "## 5. 矛盾、不确定之处与待补证据\n- <尚待验证的边界或缺口>\n"
        ),
        dry_run,
    )
    write_if_missing(
        week_dir / "judgment.md",
        (
            "<!-- AUTHORED_BY: HUMAN -->\n"
            f"# 本周判断页（{week}）\n\n"
            f"- **提交人**：{student_name}｜**会议请求**：`none`\n\n"
            "1. **本周最重要的一句结论**：\n   <请用本人语言填写>\n\n"
            "2. **和上周相比，我的看法变了什么**：\n   <请用本人语言填写>\n\n"
            "3. **下一步做什么，明确放弃了哪个备选，为什么**：\n   <请用本人语言填写>\n\n"
            "4. **我最需要谁帮忙回答或拍板的一个问题**：\n   <请用本人语言填写>\n\n"
            "5. **我核对了证据页的哪些内容，改了什么**：\n   <请写明核对了哪组数字或代码>\n"
        ),
        dry_run,
    )

    if not skip_remote and not dry_run:
        remote_url = f"https://github.com/{full_repo_name}.git"
        rem = run_cmd(["git", "remote", "get-url", "origin"], cwd=repo_dir, check=False)
        if rem.returncode != 0:
            run_cmd(["git", "remote", "add", "origin", remote_url], cwd=repo_dir)
        else:
            existing_url = rem.stdout.strip()
            if full_repo_name not in existing_url:
                run_cmd(["git", "remote", "set-url", "origin", remote_url], cwd=repo_dir)

        # Pull remote if it already has commits
        ls_rem = run_cmd(["git", "ls-remote", "--heads", "origin", "main"], cwd=repo_dir, check=False)
        if ls_rem.returncode == 0 and ls_rem.stdout.strip():
            run_cmd(["git", "pull", "--rebase", "--autostash", "origin", "main"], cwd=repo_dir, check=False)


def build_agent_block(
    repo_dir: Path,
    hub_repo: str,
    hub_branch: str,
    is_global: bool,
) -> str:
    scope_rule = (
        "- **科研相关性过滤器（全局规则必守）**：只在对话涉及**姚老师课题组科研、实验、模型训练/评测、论文、科研代码或课题组产品**时触发；个人课程作业、私事、闲聊与非课题组保密数据一律静默跳过。\n"
        if is_global
        else "- **触发范围（项目级规则）**：本仓库为课题组科研项目目录，凡涉及实质科研/工程进展均适用本流程。\n"
    )
    return (
        f"{BEGIN_MARKER}\n"
        "## 姚老师课题组 · 科研记忆与周报协同协议\n\n"
        f"- **个人私有记忆库**：`{repo_dir}`\n"
        f"- **公共协议中心（动态最新要求）**：`https://raw.githubusercontent.com/{hub_repo}/{hub_branch}/protocol/agent-instructions.md`（版本清单 `protocol/version.json`）\n"
        f"{scope_rule}"
        "- **开工前同步规范**：处理课题组任务或准备周报前，先运行 `python3 ~/.yao-research/scripts/sync_report.py --repo-dir "
        f"{repo_dir} --mode pull-protocol` 拉取最新截止时间、模板与提示词，并检查 `reviews/` 下是否有老师新批示。\n"
        "- **收工无感沉淀**：实质科研对话结束时，按协议静默追加当日流水到 `daily/YYYY-MM-DD.md` 并覆写 `projects/<slug>.md`（≤80行），随后执行 `python3 ~/.yao-research/scripts/sync_report.py --repo-dir "
        f"{repo_dir} --mode daily --push`。\n"
        "- **周报人机分离铁律**：AI 负责从日志/代码/数据客观生成 `weekly/<YYYY-Www>/evidence.md`（必须含失败尝试与出处），`judgment.md` 必须由本人用 5 问引导独立作答确认，严禁 AI 替人编造判断。周报提交前运行 `--mode weekly --week <YYYY-Www> --push` 校验。\n"
        f"{END_MARKER}\n"
    )


def upsert_marked_block(target_file: Path, block: str, dry_run: bool) -> str:
    existing = target_file.read_text(encoding="utf-8") if target_file.exists() else ""
    pattern = re.compile(
        re.escape(BEGIN_MARKER) + r".*?" + re.escape(END_MARKER) + r"\n?",
        re.DOTALL,
    )
    if pattern.search(existing):
        updated = pattern.sub(block, existing, count=1)
        action = "updated"
    else:
        sep = "\n\n" if existing and not existing.endswith("\n\n") else ("" if not existing else "\n")
        updated = existing + sep + block
        action = "appended" if existing else "created"

    if dry_run:
        print(f"[dry-run] Would mark-{action} {target_file}")
    else:
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(updated, encoding="utf-8")
        print(f"[ok] 已将协议块 ({action}) 写入：{target_file}")
    return action


def install_agent_rules(
    repo_dir: Path,
    hub_repo: str,
    hub_branch: str,
    project_dir: Optional[Path],
    use_global: bool,
    targets: Optional[str],
    dry_run: bool,
) -> List[Path]:
    touched: List[Path] = []
    if project_dir is not None:
        block = build_agent_block(repo_dir, hub_repo, hub_branch, is_global=False)
        for fname in ("AGENTS.md", "CLAUDE.md"):
            p = project_dir / fname
            upsert_marked_block(p, block, dry_run)
            touched.append(p)
        return touched

    if use_global:
        block = build_agent_block(repo_dir, hub_repo, hub_branch, is_global=True)
        home = Path.home()
        target_map = {
            "claude": home / ".claude" / "CLAUDE.md",
            "codex": home / ".codex" / "AGENTS.md",
            "gemini": home / ".gemini" / "GEMINI.md",
        }
        selected: List[Path] = []
        if targets:
            for item in targets.split(","):
                key = item.strip().lower()
                if key in target_map:
                    selected.append(target_map[key])
                else:
                    raise ValueError(f"Unknown target '{key}'. Allowed: claude, codex, gemini.")
        else:
            for path in target_map.values():
                if path.exists():
                    selected.append(path)
            if not selected:
                print(
                    "[warn] 未检测到已存在的 ~/.claude/CLAUDE.md、~/.codex/AGENTS.md 或 ~/.gemini/GEMINI.md；"
                    "若需创建全局规则请显式传 --targets claude,codex,gemini。"
                )
        for p in selected:
            upsert_marked_block(p, block, dry_run)
            touched.append(p)
    return touched


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="初始化姚老师课题组个人私有科研仓库并装配 Agent 规则（零第三方依赖）"
    )
    parser.add_argument("--name", required=True, help="学生中文姓名，如 姜博文")
    parser.add_argument("--slug", required=True, help="拼音标识（用作私库后缀 yao-research-<slug>）")
    parser.add_argument("--role", default="phd", choices=["phd", "master", "other"], help="身份角色")
    parser.add_argument("--topic", default="待补充课题", help="当前主课题名称")
    parser.add_argument("--partner", default="待分配", help="课题组结对成员")
    parser.add_argument("--code-repos", default="", help="关联科研代码仓库 URL 或路径")
    parser.add_argument("--product", default="no", choices=["yes", "no"], help="是否承担每周产品任务")
    parser.add_argument("--project-dir", default=None, help="指定科研项目目录（写入项目级 AGENTS.md/CLAUDE.md）")
    parser.add_argument("--global", dest="use_global", action="store_true", help="写入全局 Agent 规则文件（带科研过滤）")
    parser.add_argument("--targets", default=None, help="全局安装目标（逗号分隔：claude,codex,gemini）")
    parser.add_argument("--no-agent-md", action="store_true", help="跳过写入任何 AGENTS.md / CLAUDE.md")
    parser.add_argument("--repo-dir", default="~/.yao-research/repo", help="本地私有记忆库目录")
    parser.add_argument("--week", default=None, help="初始周报周次（默认取 version.json 或当前 ISO 周）")
    parser.add_argument("--hub-dir", default=None, help="本地协议中心目录（离线/测试用）")
    parser.add_argument("--hub-repo", default=DEFAULT_HUB_REPO, help="公共协议中心 GitHub 仓库")
    parser.add_argument("--hub-branch", default=DEFAULT_HUB_BRANCH, help="公共协议中心分支")
    parser.add_argument("--skip-remote", action="store_true", help="跳过远端 GitHub 建库与协作者邀请")
    parser.add_argument("--dry-run", action="store_true", help="仅预览将执行的步骤，不写入文件或调用远端写接口")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    slug = sanitize_slug(args.slug)
    repo_dir = Path(args.repo_dir).expanduser().resolve()
    hub_dir = Path(args.hub_dir).expanduser().resolve() if args.hub_dir else None
    project_dir = Path(args.project_dir).expanduser().resolve() if args.project_dir else None

    manifest = load_version_manifest(hub_dir, args.hub_repo, args.hub_branch)
    repo_prefix = manifest.get("repo_prefix", "yao-research-")
    collaborators = manifest.get("required_collaborators", [])
    week = args.week or manifest.get("current_week") or current_iso_week()
    repo_name = f"{repo_prefix}{slug}"

    owner = "<your-github-username>"
    full_repo_name = f"{owner}/{repo_name}"

    if not args.skip_remote:
        ok, info = check_gh_ready()
        if not ok:
            print(f"[error] {info}", file=sys.stderr)
            print_manual_github_guide(slug, repo_prefix, repo_dir, collaborators)
            return 2
        owner = info
        full_repo_name = ensure_private_remote_repo(
            owner=owner,
            repo_name=repo_name,
            student_name=args.name,
            collaborators=collaborators,
            dry_run=args.dry_run,
        )
    else:
        print("[info] 已跳过远端 GitHub 操作（--skip-remote）。")

    init_local_repo(
        repo_dir=repo_dir,
        owner=owner,
        full_repo_name=full_repo_name,
        student_name=args.name,
        slug=slug,
        role=args.role,
        topic=args.topic,
        partner=args.partner,
        code_repos=args.code_repos,
        has_product=args.product,
        week=week,
        hub_repo=args.hub_repo,
        hub_branch=args.hub_branch,
        skip_remote=args.skip_remote,
        dry_run=args.dry_run,
    )

    if not args.no_agent_md:
        install_agent_rules(
            repo_dir=repo_dir,
            hub_repo=args.hub_repo,
            hub_branch=args.hub_branch,
            project_dir=project_dir,
            use_global=args.use_global,
            targets=args.targets,
            dry_run=args.dry_run,
        )

    print(f"[done] 初始化完成：本地目录={repo_dir}，私有库={full_repo_name}，本周={week}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
