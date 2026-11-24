#!/usr/bin/env python3
"""Yao Research Group unified sync CLI (zero external dependencies, Python 3.9+).

Commands:
  init       Zero-prompt initialize private student repo (~/.yao-research/repo) and agent rules.
  preflight  Auto-pull hub (throttled), migrate schema, evaluate schedule.json window & reviews.
  push       Run privacy/secret/size/report gates, commit locally, and push (offline-resilient).
  status     Show current repo privacy, protocol version, schedule state, and offline queue.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import getpass
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
import urllib.request

TZ_SHANGHAI = timezone(timedelta(hours=8))
DEFAULT_HUB_REPO = "Yao-Research-Group/research-hub"
DEFAULT_HUB_BRANCH = "main"
DEFAULT_REPO_NAME = "yao-research-memory"
BEGIN_MARKER = "<!-- BEGIN YAO-RESEARCH-HUB -->"
END_MARKER = "<!-- END YAO-RESEARCH-HUB -->"
MAX_SINGLE_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB
PULL_THROTTLE_SECONDS = 600  # 10 minutes

SECRET_PATTERN = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----"
    r"|\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"
    r"|\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}"
    r"|\bBearer\s+[A-Za-z0-9._~-]{20,}"
    r"|https?://[^\s<>]*[?&#](?:[\w-]*token|api[-_]?key|key|secret|password|"
    r"[\w-]*signature|sig|auth)=[^\s&#<>]+",
    re.IGNORECASE,
)

FORBIDDEN_EXTS = {".env", ".pem", ".key", ".p12", ".pfx", ".pt", ".pth", ".ckpt", ".safetensors"}
VALID_MEETING_REQUESTS = {"none", "group", "1on1", "all"}

UNFILLED_PLACEHOLDERS = [
    "<请用本人语言填写>",
    "<请写明核对了哪组数字或代码>",
    "<请由 AI 从实际日志与实验产出整理，附来源>",
    "<概括本周最实质的推进或核心判断，附来源>",
    "<实验协议、对照基线与真实数字",
    "<必填：本周跑不通、负提升、被证伪的做法",
    "<必填：本周未成功、负提升或被证伪的做法>",
]

STUDENT_GITIGNORE = """# Secrets & local configs
.env
.env.*
*.key
*.pem
*.p12
*.pfx
secrets/
*.local.json

# Large model weights & datasets
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


def run_cmd(
    cmd: List[str],
    cwd: Optional[Path] = None,
    check: bool = True,
    timeout: Optional[int] = None,
) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        msg = proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}"
        raise RuntimeError(f"Command failed ({' '.join(cmd)}): {msg}")
    return proc


def now_shanghai(override: Optional[str] = None) -> datetime:
    if override:
        dt = datetime.fromisoformat(override)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ_SHANGHAI)
        return dt.astimezone(TZ_SHANGHAI)
    return datetime.now(TZ_SHANGHAI)


def iso_week_str(dt: datetime) -> str:
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def sanitize_slug(raw: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", raw.strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-_")
    return slug or "research-project"


def parse_simple_yaml(text: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        val = v.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        result[k.strip()] = val
    return result


def default_hub_dir() -> Path:
    script_hub = Path(__file__).resolve().parents[1]
    if (script_hub / "protocol" / "version.json").is_file():
        return script_hub
    return Path("~/.yao-research/hub").expanduser().resolve()


def load_hub_configs(hub_dir: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    version_path = hub_dir / "protocol" / "version.json"
    schedule_path = hub_dir / "protocol" / "schedule.json"
    version = json.loads(version_path.read_text(encoding="utf-8")) if version_path.is_file() else {}
    schedule = json.loads(schedule_path.read_text(encoding="utf-8")) if schedule_path.is_file() else {}
    return version, schedule


def maybe_pull_hub(hub_dir: Path, force_pull: bool = False, skip_remote: bool = False) -> str:
    if skip_remote or not (hub_dir / ".git").exists():
        return "skipped"
    origin_chk = run_cmd(["git", "remote", "get-url", "origin"], cwd=hub_dir, check=False)
    if origin_chk.returncode != 0 or not origin_chk.stdout.strip():
        return "no-remote"

    state_file = hub_dir.parent / ".hub-pull-state.json"
    now_ts = time.time()
    if not force_pull and state_file.is_file():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            if now_ts - float(state.get("last_pull_ts", 0)) < PULL_THROTTLE_SECONDS:
                return "throttled"
        except Exception:
            pass

    try:
        proc = run_cmd(["git", "pull", "--ff-only"], cwd=hub_dir, check=False, timeout=4)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps({"last_pull_ts": now_ts, "returncode": proc.returncode}) + "\n",
            encoding="utf-8",
        )
        if proc.returncode == 0:
            return "updated"
        return f"offline-fallback ({proc.stderr.strip()[:80]})"
    except Exception as exc:
        return f"offline-fallback ({exc})"


def detect_ai_tools(home: Path) -> List[str]:
    tools: List[str] = []
    if (home / ".claude").is_dir():
        tools.append("claude")
    if (home / ".codex").is_dir():
        tools.append("codex")
    if (home / ".gemini").is_dir():
        tools.append("gemini")
    if (home / ".cursor").is_dir():
        tools.append("cursor")
    return tools


def detect_cwd_repo(cwd: Path, repo_dir: Path, hub_dir: Path) -> Dict[str, str]:
    resolved = cwd.resolve()
    if resolved == repo_dir.resolve() or resolved == hub_dir.resolve():
        return {"cwd": str(resolved), "project_name": resolved.name, "git_remote": "", "git_branch": ""}
    info: Dict[str, str] = {"cwd": str(resolved), "project_name": resolved.name, "git_remote": "", "git_branch": ""}
    top = run_cmd(["git", "rev-parse", "--show-toplevel"], cwd=resolved, check=False)
    if top.returncode == 0 and top.stdout.strip():
        top_path = Path(top.stdout.strip()).resolve()
        if top_path not in (repo_dir.resolve(), hub_dir.resolve()):
            info["project_name"] = top_path.name
            rem = run_cmd(["git", "remote", "get-url", "origin"], cwd=top_path, check=False)
            if rem.returncode == 0:
                info["git_remote"] = rem.stdout.strip()
            br = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=top_path, check=False)
            if br.returncode == 0:
                info["git_branch"] = br.stdout.strip()
    return info


def detect_identity(skip_remote: bool) -> Dict[str, str]:
    login = ""
    display_name = ""
    email = ""
    if not skip_remote and shutil.which("gh"):
        u = run_cmd(["gh", "api", "user"], check=False, timeout=8)
        if u.returncode == 0 and u.stdout.strip():
            try:
                data = json.loads(u.stdout)
                login = str(data.get("login") or "").strip()
                display_name = str(data.get("name") or "").strip()
                email = str(data.get("email") or "").strip()
            except Exception:
                pass
    if not display_name:
        gn = run_cmd(["git", "config", "user.name"], check=False)
        if gn.returncode == 0 and gn.stdout.strip():
            display_name = gn.stdout.strip()
    if not email:
        ge = run_cmd(["git", "config", "user.email"], check=False)
        if ge.returncode == 0 and ge.stdout.strip():
            email = ge.stdout.strip()
    if not login:
        try:
            login = getpass.getuser()
        except Exception:
            login = "student"
    if not display_name:
        display_name = login
    if not email:
        email = f"{login}@users.noreply.github.com"

    return {
        "github_login": login,
        "display_name": display_name,
        "email": email,
        "hostname": socket.gethostname(),
        "os": f"{platform.system()} {platform.release()}",
        "ai_tools": ",".join(detect_ai_tools(Path.home())),
    }


def check_gh_ready() -> Tuple[bool, str]:
    if not shutil.which("gh"):
        return False, "GitHub CLI (gh) 未安装。"
    auth = run_cmd(["gh", "auth", "status"], check=False)
    if auth.returncode != 0:
        return False, "GitHub CLI (gh) 尚未登录（请先执行 gh auth login）。"
    u = run_cmd(["gh", "api", "user", "--jq", ".login"], check=False)
    if u.returncode != 0 or not u.stdout.strip():
        return False, f"无法读取 GitHub 登录用户：{u.stderr.strip()}"
    return True, u.stdout.strip()


def print_manual_github_guide(repo_name: str, repo_dir: Path, collaborators: List[Dict[str, str]]) -> None:
    collab_names = ", ".join(c["github"] for c in collaborators)
    print(
        "\n[需要登录 gh 或按 3 步在网页建私有库]\n"
        f"  1) 若有 gh，运行 gh auth login 后重跑本命令；\n"
        f"  2) 或打开 https://github.com/new ，仓库名填 {repo_name}，可见性必须选【Private】，\n"
        f"     然后在 Settings -> Collaborators 添加：{collab_names}；\n"
        f"  3) 执行：python3 ~/.yao-research/hub/scripts/yao_sync.py init --skip-remote\n"
        f"     cd {repo_dir} && git remote add origin https://github.com/<你的GitHub账号>/{repo_name}.git && git push -u origin main\n",
        file=sys.stderr,
    )


def parse_github_slug(remote_url: str) -> Optional[str]:
    m = re.search(r"github\.com[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?$", remote_url.strip())
    return m.group(1) if m else None


def verify_repo_not_public(full_name: str) -> None:
    """Verify repository is private via gh if available, or unauthenticated GitHub API probe."""
    if shutil.which("gh"):
        auth = run_cmd(["gh", "auth", "status"], check=False)
        if auth.returncode == 0:
            probe = run_cmd(["gh", "api", f"repos/{full_name}"], check=False, timeout=8)
            if probe.returncode == 0:
                meta = json.loads(probe.stdout)
                if not meta.get("private", False):
                    raise RuntimeError(
                        f"安全拦截：远端仓库 {full_name} 当前为 PUBLIC！已阻止写入，请立即转为 Private。"
                    )
                return

    # Fallback check without gh: unauthenticated GET returns 200 ONLY for public repos
    url = f"https://api.github.com/repos/{full_name}"
    req = urllib.request.Request(url, headers={"User-Agent": "yao-research-hub/1.1"})
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            meta = json.loads(resp.read().decode("utf-8"))
            if not meta.get("private", False):
                raise RuntimeError(
                    f"安全拦截：远端仓库 {full_name} 可被匿名公开访问（PUBLIC）！已阻止写入。"
                )
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return  # 404 on unauthenticated GitHub API confirms repo is private (or non-existent)
    except Exception:
        pass


def ensure_private_remote_repo(
    owner: str,
    repo_name: str,
    display_name: str,
    collaborators: List[Dict[str, str]],
    existing_origin_slug: Optional[str],
    dry_run: bool,
) -> str:
    full_name = existing_origin_slug or f"{owner}/{repo_name}"
    if dry_run:
        print(f"[dry-run] Would ensure private repo {full_name} and invite collaborators.")
        return full_name

    probe = run_cmd(["gh", "api", f"repos/{full_name}"], check=False)
    if probe.returncode == 0:
        meta = json.loads(probe.stdout)
        if not meta.get("private", False):
            raise RuntimeError(f"安全拦截：仓库 {full_name} 为 PUBLIC，禁止写入科研笔记。")
        print(f"[ok] 复用已有私有仓库：{full_name}")
    else:
        desc = f"Yao Research Group 个人私有科研笔记与周报库（{display_name}）"
        run_cmd(["gh", "repo", "create", full_name, "--private", "--description", desc])
        verify = json.loads(run_cmd(["gh", "api", f"repos/{full_name}"]).stdout)
        if not verify.get("private", False):
            raise RuntimeError(f"安全拦截：新建仓库 {full_name} 未处于 Private 状态！")
        print(f"[ok] 已创建私有仓库：{full_name}")

    repo_owner = full_name.split("/")[0]
    for item in collaborators:
        collab = item["github"]
        perm = item.get("permission", "push")
        if collab.lower() == repo_owner.lower():
            continue
        proc = run_cmd(
            ["gh", "api", "-X", "PUT", f"repos/{full_name}/collaborators/{collab}", "-f", f"permission={perm}"],
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"添加协作者 {collab} 失败：{proc.stderr.strip() or proc.stdout.strip()}")
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


def migrate_notebook_schema(
    repo_dir: Path,
    hub_dir: Path,
    version_cfg: Dict[str, Any],
    identity: Dict[str, str],
    cwd_info: Dict[str, str],
    full_repo_name: Optional[str] = None,
    dry_run: bool = False,
) -> None:
    """Non-destructive migration: ensure v2 dirs (logs/, projects/, weekly/, reviews/) and .yao-notebook.json."""
    if not dry_run:
        for sub in ("logs", "projects", "weekly", "reviews"):
            d = repo_dir / sub
            d.mkdir(parents=True, exist_ok=True)
            if not any(d.iterdir()):
                (d / ".gitkeep").write_text("", encoding="utf-8")

    nb_file = repo_dir / ".yao-notebook.json"
    existing: Dict[str, Any] = {}
    if nb_file.is_file():
        try:
            existing = json.loads(nb_file.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    known_repos = set(existing.get("observed_code_repos", []))
    if cwd_info.get("git_remote"):
        known_repos.add(cwd_info["git_remote"])

    payload = {
        "schema_version": int(version_cfg.get("notebook_schema_version", 2)),
        "hub_repo": version_cfg.get("hub_repo", DEFAULT_HUB_REPO),
        "hub_dir": str(hub_dir),
        "repo": full_repo_name or existing.get("repo", ""),
        "installed_at": existing.get("installed_at") or now_shanghai().isoformat(timespec="seconds"),
        "last_seen_at": now_shanghai().isoformat(timespec="seconds"),
        "identity": {
            "github_login": existing.get("identity", {}).get("github_login") or identity["github_login"],
            "display_name": existing.get("identity", {}).get("display_name") or identity["display_name"],
            "email": existing.get("identity", {}).get("email") or identity["email"],
        },
        "env": {
            "hostname": identity["hostname"],
            "os": identity["os"],
            "ai_tools": identity["ai_tools"],
            "last_cwd": cwd_info["cwd"],
            "last_project": cwd_info["project_name"],
        },
        "observed_code_repos": sorted(r for r in known_repos if r),
    }
    if not dry_run:
        nb_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_agent_block(hub_dir: Path, repo_dir: Path, is_global: bool) -> str:
    scope_line = (
        "- **适用范围**：仅在对话涉及课题组科研、实验、代码调试、论文阅读或产品原型时触发；"
        "普通闲聊、个人课程与私事直接跳过，不扫描无关个人目录。\n"
        if is_global
        else "- **适用范围**：本目录为课题组科研项目，凡实质科研/工程推进结束时均适用。\n"
    )
    return (
        f"{BEGIN_MARKER}\n"
        "## 个人科研笔记与周报同步（Yao Research Group）\n\n"
        f"- 协议中心：`{hub_dir}`（最新规范见 `protocol/CURRENT.md`）｜个人私有库：`{repo_dir}`\n"
        f"{scope_line}"
        f"- **每次科研对话收尾**：先运行 `python3 {hub_dir}/scripts/yao_sync.py preflight` 同步最新规范与排程状态，"
        f"再按 `{hub_dir}/protocol/CURRENT.md` 追加当日笔记（及排程触发时的周报）并运行 `python3 {hub_dir}/scripts/yao_sync.py push`。\n"
        "- **透明告知**：同步完成后在回复末尾附一行极简回执（如 `📝 已同步本次科研记录至个人私有库`）；绝不上传密钥或大文件。\n"
        f"{END_MARKER}\n"
    )


def upsert_marked_block(target_file: Path, block: str, dry_run: bool, cursor_frontmatter: bool = False) -> str:
    existing = target_file.read_text(encoding="utf-8") if target_file.exists() else ""
    pattern = re.compile(re.escape(BEGIN_MARKER) + r".*?" + re.escape(END_MARKER) + r"\n?", re.DOTALL)
    if pattern.search(existing):
        updated = pattern.sub(block, existing, count=1)
        action = "updated"
    else:
        prefix = ""
        if not existing and cursor_frontmatter:
            prefix = "---\ndescription: Yao Research Group personal research notebook sync\nalwaysApply: true\n---\n\n"
        sep = "\n\n" if existing and not existing.endswith("\n\n") else ("" if not existing else "\n")
        updated = prefix + existing + sep + block
        action = "appended" if existing else "created"

    if dry_run:
        print(f"[dry-run] Would {action} {target_file}")
    else:
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(updated, encoding="utf-8")
        print(f"[ok] 已写入协议规则 ({action})：{target_file}")
    return action


def install_agent_rules(
    hub_dir: Path,
    repo_dir: Path,
    project_dir: Optional[Path],
    targets_arg: Optional[str],
    dry_run: bool,
) -> List[Path]:
    touched: List[Path] = []
    if project_dir is not None:
        block = build_agent_block(hub_dir, repo_dir, is_global=False)
        for fname in ("AGENTS.md", "CLAUDE.md"):
            p = project_dir / fname
            upsert_marked_block(p, block, dry_run)
            touched.append(p)
        return touched

    block = build_agent_block(hub_dir, repo_dir, is_global=True)
    home = Path.home()
    tool_targets = {
        "claude": (home / ".claude", home / ".claude" / "CLAUDE.md", False),
        "codex": (home / ".codex", home / ".codex" / "AGENTS.md", False),
        "gemini": (home / ".gemini", home / ".gemini" / "GEMINI.md", False),
        "cursor": (home / ".cursor", home / ".cursor" / "rules" / "yao-research-hub.mdc", True),
    }
    selected: List[Tuple[Path, bool]] = []
    if targets_arg:
        for item in targets_arg.split(","):
            k = item.strip().lower()
            if k not in tool_targets:
                raise ValueError(f"Unknown target '{k}'. Allowed: claude, codex, gemini, cursor.")
            _, rule_file, is_cursor = tool_targets[k]
            selected.append((rule_file, is_cursor))
    else:
        for _, (tool_dir, rule_file, is_cursor) in tool_targets.items():
            if tool_dir.is_dir() or rule_file.is_file():
                selected.append((rule_file, is_cursor))
        if not selected:
            # Bare environment fallback: create Claude & Codex global rule files
            selected = [
                (home / ".claude" / "CLAUDE.md", False),
                (home / ".codex" / "AGENTS.md", False),
            ]

    for path, is_cursor in selected:
        upsert_marked_block(path, block, dry_run, cursor_frontmatter=is_cursor)
        touched.append(path)
    return touched


def strip_markdown_chrome(text: str) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    lines: List[str] = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if re.match(r"^[0-9]+\.\s*\*\*.*\*\*：?$", s):
            continue
        if s.startswith("- **提交人**") or s.startswith("- **要求**") or s.startswith("- **硬性约束**"):
            continue
        lines.append(s)
    return "\n".join(lines).strip()


def is_valid_weekly_submission(repo_dir: Path, week: str, require_human_judgment: bool) -> Tuple[bool, str]:
    wdir = repo_dir / "weekly" / week
    if not wdir.is_dir():
        return False, "missing"

    report_p = wdir / "report.md"
    ev_p = wdir / "evidence.md"
    jd_p = wdir / "judgment.md"

    has_ev = ev_p.is_file() and len(strip_markdown_chrome(ev_p.read_text(encoding="utf-8"))) >= 80 and not any(
        tok in ev_p.read_text(encoding="utf-8") for tok in UNFILLED_PLACEHOLDERS
    )
    has_jd = jd_p.is_file() and len(strip_markdown_chrome(jd_p.read_text(encoding="utf-8"))) >= 40 and not any(
        tok in jd_p.read_text(encoding="utf-8") for tok in UNFILLED_PLACEHOLDERS
    )
    has_rep = report_p.is_file() and len(strip_markdown_chrome(report_p.read_text(encoding="utf-8"))) >= 80 and not any(
        tok in report_p.read_text(encoding="utf-8") for tok in UNFILLED_PLACEHOLDERS
    )

    if require_human_judgment:
        if has_ev and has_jd:
            return True, "submitted_human"
        if has_ev or has_rep:
            return False, "missing_judgment"
        return False, "missing"

    if has_rep:
        return True, "submitted_ai_mvp"
    if has_ev and has_jd:
        return True, "submitted_human"
    if has_ev:
        return True, "submitted_evidence_only"
    return False, "missing"


def evaluate_schedule(schedule: Dict[str, Any], dt: datetime, repo_dir: Path) -> Dict[str, Any]:
    boot = schedule.get("bootstrap_week", {})
    active_week: Optional[str] = None
    deadline_str = ""
    require_human = False
    window_reason = ""

    if boot.get("window_start") and boot.get("grace_end"):
        ws = datetime.fromisoformat(boot["window_start"])
        ge = datetime.fromisoformat(boot["grace_end"])
        if ws <= dt <= ge:
            active_week = boot["week"]
            deadline_str = boot.get("deadline", "")
            require_human = bool(boot.get("require_human_judgment", False))
            window_reason = "bootstrap_week"

    reg = schedule.get("regular_weekly", {})
    if active_week is None and reg.get("enabled", True):
        wd = int(reg.get("deadline_weekday", 0))
        hr = int(reg.get("deadline_hour", 18))
        mn = int(reg.get("deadline_minute", 0))
        before_h = int(reg.get("window_hours_before", 48))
        after_h = int(reg.get("grace_hours_after", 24))
        require_human = bool(reg.get("require_human_judgment", False))

        # Check current week's and next/prev week's candidate deadlines
        base_midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        for day_offset in range(-7, 8):
            cand_day = base_midnight + timedelta(days=day_offset)
            if cand_day.weekday() != wd:
                continue
            cand_dl = cand_day.replace(hour=hr, minute=mn)
            if (cand_dl - timedelta(hours=before_h)) <= dt <= (cand_dl + timedelta(hours=after_h)):
                active_week = iso_week_str(cand_dl)
                deadline_str = cand_dl.isoformat(timespec="seconds")
                window_reason = "regular_weekly"
                break

    in_window = active_week is not None
    check_week = active_week or iso_week_str(dt)
    submitted, sub_state = is_valid_weekly_submission(repo_dir, check_week, require_human)

    action = "WEEKLY_REPORT_DUE" if (in_window and not submitted) else "NONE"
    return {
        "in_window": in_window,
        "window_reason": window_reason or "outside_window",
        "active_week": check_week,
        "deadline": deadline_str,
        "require_human_judgment": require_human,
        "submission_state": sub_state,
        "action": action,
    }


def pending_reviews(repo_dir: Path) -> List[str]:
    rev_dir = repo_dir / "reviews"
    if not rev_dir.is_dir():
        return []
    pending: List[str] = []
    for f in sorted(rev_dir.glob("*-advisor.md")) + sorted(rev_dir.glob("*-ai-review.md")):
        wk = f.name.split("-advisor.md")[0].split("-ai-review.md")[0]
        if not (rev_dir / f"{wk}-reply.md").is_file():
            pending.append(f.name)
    return pending


def ahead_commit_count(repo_dir: Path) -> int:
    if not (repo_dir / ".git").exists():
        return 0
    proc = run_cmd(["git", "rev-list", "--count", "@{u}..HEAD"], cwd=repo_dir, check=False)
    if proc.returncode == 0 and proc.stdout.strip().isdigit():
        return int(proc.stdout.strip())
    return 0


def needs_schema_migration(repo_dir: Path, target_version: int) -> bool:
    for sub in ("logs", "projects", "weekly", "reviews"):
        if not (repo_dir / sub).is_dir():
            return True
    nb_file = repo_dir / ".yao-notebook.json"
    if not nb_file.is_file():
        return True
    try:
        data = json.loads(nb_file.read_text(encoding="utf-8"))
        return int(data.get("schema_version", 0)) < target_version
    except Exception:
        return True


def cmd_preflight(args: argparse.Namespace) -> int:
    hub_dir = Path(args.hub_dir).expanduser().resolve() if args.hub_dir else default_hub_dir()
    repo_dir = Path(args.repo_dir).expanduser().resolve()

    pull_status = maybe_pull_hub(hub_dir, force_pull=args.force_pull, skip_remote=args.skip_remote)
    version_cfg, schedule = load_hub_configs(hub_dir)
    target_ver = int(version_cfg.get("notebook_schema_version", 2))

    if repo_dir.is_dir() and needs_schema_migration(repo_dir, target_ver):
        identity = detect_identity(skip_remote=True)
        cwd_info = detect_cwd_repo(Path.cwd(), repo_dir, hub_dir)
        migrate_notebook_schema(repo_dir, hub_dir, version_cfg, identity, cwd_info)

    dt = now_shanghai(args.now)
    sched_res = evaluate_schedule(schedule, dt, repo_dir)
    reviews = pending_reviews(repo_dir)
    ahead = ahead_commit_count(repo_dir)

    result = {
        "hub_dir": str(hub_dir),
        "repo_dir": str(repo_dir),
        "schema_version": version_cfg.get("schema_version", "unknown"),
        "pull_status": pull_status,
        "current_doc": str(hub_dir / "protocol" / "CURRENT.md"),
        "pending_reviews": reviews,
        "unpushed_commits": ahead,
        **sched_res,
    }
    if getattr(args, "json_out", False):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"[preflight] 协议版本: v{result['schema_version']} ({pull_status}) | 周次: {result['active_week']}")
        print(f"[preflight] 规范文件: {result['current_doc']}")
        print(f"[preflight] ACTION: {result['action']} (窗口={result['window_reason']}, 周报状态={result['submission_state']}, 需人工判断={result['require_human_judgment']})")
        if result["deadline"]:
            print(f"[preflight] 截止时间: {result['deadline']}")
        if reviews:
            print(f"[preflight] 未回复导师/AI批示: {', '.join(reviews)}")
        if ahead > 0:
            print(f"[preflight] 本地待补推提交数: {ahead}")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    hub_dir = Path(args.hub_dir).expanduser().resolve() if args.hub_dir else default_hub_dir()
    repo_dir = Path(args.repo_dir).expanduser().resolve()
    project_dir = Path(args.project_dir).expanduser().resolve() if args.project_dir else None

    if project_dir is None and not getattr(args, "use_global", False) and not args.no_agent_md:
        print(
            "[ACTION_REQUIRED] 安装前请先让用户二选一确认规则安装范围（不问姓名/拼音等其他问题）：\n"
            '  ① 只装到一个指定的科研工作目录（推荐）：请用户明确给出目录路径后加 --project-dir "<目录路径>"；\n'
            "  ② 全局安装：加 --global；\n"
            "  ③ 仅建库不写入任何规则：加 --no-agent-md。",
            file=sys.stderr,
        )
        return 2

    if project_dir is not None and not project_dir.is_dir():
        print(
            f"[error] 指定的科研工作目录不存在：{project_dir}。请让用户明确提供正确目录路径（不可自动猜测），或改用 --global。",
            file=sys.stderr,
        )
        return 2

    maybe_pull_hub(hub_dir, force_pull=False, skip_remote=args.skip_remote)
    version_cfg, _ = load_hub_configs(hub_dir)
    repo_name = args.repo_name or version_cfg.get("default_repo_name", DEFAULT_REPO_NAME)
    collaborators = version_cfg.get("required_collaborators", [
        {"github": "taoyao2005", "role": "advisor", "permission": "push"},
        {"github": "treerobin06", "role": "secretary", "permission": "push"},
    ])
    identity = detect_identity(skip_remote=args.skip_remote)
    cwd_info = detect_cwd_repo(Path.cwd(), repo_dir, hub_dir)

    existing_origin_slug: Optional[str] = None
    if (repo_dir / ".git").exists():
        rem = run_cmd(["git", "remote", "get-url", "origin"], cwd=repo_dir, check=False)
        if rem.returncode == 0 and rem.stdout.strip():
            existing_origin_slug = parse_github_slug(rem.stdout.strip())

    owner = identity["github_login"]
    full_repo_name = existing_origin_slug or f"{owner}/{repo_name}"

    if not args.skip_remote:
        ok, gh_owner = check_gh_ready()
        if not ok:
            print(f"[error] {gh_owner}", file=sys.stderr)
            print_manual_github_guide(repo_name, repo_dir, collaborators)
            return 2
        owner = gh_owner
        identity["github_login"] = owner
        advisor_logins = {
            c.get("github", "").lower() for c in collaborators if c.get("role") == "advisor"
        }
        if owner.lower() in advisor_logins:
            print(
                f"[note] 检测到当前 GitHub 账号 {owner} 为导师角色：导师只需接收同学私有库的协作者邀请，通常无需安装学生端上传规则。"
            )
        full_repo_name = ensure_private_remote_repo(
            owner=owner,
            repo_name=repo_name,
            display_name=identity["display_name"],
            collaborators=collaborators,
            existing_origin_slug=existing_origin_slug,
            dry_run=args.dry_run,
        )
    else:
        print("[info] 跳过远端 GitHub 建库（--skip-remote）。")

    if not args.dry_run:
        repo_dir.mkdir(parents=True, exist_ok=True)
        if not (repo_dir / ".git").exists():
            run_cmd(["git", "init", "-b", "main"], cwd=repo_dir)
        if not run_cmd(["git", "config", "user.name"], cwd=repo_dir, check=False).stdout.strip():
            run_cmd(["git", "config", "user.name", identity["display_name"]], cwd=repo_dir)
        if not run_cmd(["git", "config", "user.email"], cwd=repo_dir, check=False).stdout.strip():
            run_cmd(["git", "config", "user.email", identity["email"]], cwd=repo_dir)

    write_if_missing(repo_dir / ".gitignore", STUDENT_GITIGNORE, args.dry_run)
    write_if_missing(
        repo_dir / "README.md",
        (
            f"# {identity['display_name']} · 个人私有科研记忆与周报仓库\n\n"
            f"- 协议中心：`{version_cfg.get('hub_repo', DEFAULT_HUB_REPO)}`\n"
            "- 可见性：Private（仅本人、导师与会议秘书可见）\n"
            "- 日常记录：`logs/YYYY-MM-DD.md` · 项目活页：`projects/<slug>.md` · 周报：`weekly/YYYY-Www/` · 批示：`reviews/`\n"
        ),
        args.dry_run,
    )
    write_if_missing(
        repo_dir / "profile.md",
        (
            "# 成员档案\n\n"
            f"- **姓名 / 称呼**：{identity['display_name']}\n"
            f"- **GitHub ID**：{owner}\n"
            f"- **仓库地址**：{full_repo_name}\n"
            f"- **主机与系统**：{identity['hostname']} ({identity['os']})\n"
            f"- **本机 AI 工具**：{identity['ai_tools'] or 'unknown'}\n"
            f"- **初始工作目录**：{cwd_info['cwd']}\n"
            f"- **关联代码仓库**：{cwd_info['git_remote'] or '自动随对话记录补充'}\n"
        ),
        args.dry_run,
    )
    migrate_notebook_schema(repo_dir, hub_dir, version_cfg, identity, cwd_info, full_repo_name, dry_run=args.dry_run)

    if not args.skip_remote and not args.dry_run:
        remote_url = f"https://github.com/{full_repo_name}.git"
        rem = run_cmd(["git", "remote", "get-url", "origin"], cwd=repo_dir, check=False)
        if rem.returncode != 0:
            run_cmd(["git", "remote", "add", "origin", remote_url], cwd=repo_dir)
        elif full_repo_name not in rem.stdout.strip():
            run_cmd(["git", "remote", "set-url", "origin", remote_url], cwd=repo_dir)
        ls_rem = run_cmd(["git", "ls-remote", "--heads", "origin", "main"], cwd=repo_dir, check=False, timeout=8)
        if ls_rem.returncode == 0 and ls_rem.stdout.strip():
            run_cmd(["git", "pull", "--rebase", "--autostash", "origin", "main"], cwd=repo_dir, check=False, timeout=10)

    if not args.no_agent_md:
        install_agent_rules(
            hub_dir=hub_dir,
            repo_dir=repo_dir,
            project_dir=project_dir,
            targets_arg=args.targets,
            dry_run=args.dry_run,
        )

    print(f"[done] 初始化完成：本地={repo_dir} | 私有库={full_repo_name}")
    if not args.dry_run:
        pf_ns = argparse.Namespace(
            hub_dir=str(hub_dir),
            repo_dir=str(repo_dir),
            force_pull=False,
            skip_remote=True,
            json_out=False,
            now=getattr(args, "now", None),
        )
        cmd_preflight(pf_ns)
    return 0


def iter_candidate_files(repo_dir: Path) -> List[Path]:
    if (repo_dir / ".git").exists():
        proc = run_cmd(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=repo_dir, check=False)
        if proc.returncode == 0:
            return [
                repo_dir / rel.strip()
                for rel in proc.stdout.splitlines()
                if rel.strip() and (repo_dir / rel.strip()).is_file() and not (repo_dir / rel.strip()).is_symlink()
            ]
    out: List[Path] = []
    for root, dirs, names in os.walk(repo_dir):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".venv"}]
        for n in names:
            p = Path(root) / n
            if p.is_file() and not p.is_symlink():
                out.append(p)
    return out


def scan_secrets_and_sizes(repo_dir: Path, max_bytes: int) -> List[str]:
    errors: List[str] = []
    for path in iter_candidate_files(repo_dir):
        rel = path.relative_to(repo_dir)
        ext = path.suffix.lower()
        if path.name.startswith(".env") or ext in FORBIDDEN_EXTS:
            errors.append(f"禁止提交敏感或模型权重文件：{rel}")
            continue
        size = path.stat().st_size
        if size > max_bytes:
            errors.append(f"文件超过大小上限 ({size} > {max_bytes} bytes)：{rel}")
            continue
        if ext in {".md", ".yaml", ".yml", ".json", ".txt", ".py", ".sh", ".csv", ".tsv"}:
            try:
                txt = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                errors.append(f"文本文件非 UTF-8 编码：{rel}")
                continue
            for idx, line in enumerate(txt.splitlines(), start=1):
                if SECRET_PATTERN.search(line):
                    errors.append(f"检测到疑似密钥/凭据/带Token链接：{rel}:{idx}（已隐藏内容）")
    return errors


def ensure_weekly_meta_and_validate(
    repo_dir: Path,
    week: str,
    require_human: bool,
    limits: Dict[str, Any],
    identity: Dict[str, str],
    cwd_info: Dict[str, str],
) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    wdir = repo_dir / "weekly" / week
    if not wdir.is_dir():
        return [f"缺少周报目录：{wdir.relative_to(repo_dir)}"], warnings

    meta_p = wdir / "meta.yaml"
    if not meta_p.is_file():
        meta_p.write_text(
            f'week: "{week}"\n'
            f'student_name: "{identity["display_name"]}"\n'
            f'student_id: "{identity["github_login"]}"\n'
            f'github_login: "{identity["github_login"]}"\n'
            'role: "phd"\n'
            f'topic: "{cwd_info["project_name"]}"\n'
            'group_partner: "无"\n'
            'report_mode: "ai_mvp"\n'
            'judgment_status: "ai_mvp"\n'
            'meeting_request: "none"\n'
            'blockers_summary: "无"\n'
            'help_needed_from: "无"\n'
            'help_needed_what: "无"\n'
            'has_product_task: "no"\n'
            f'code_repos: "{cwd_info["git_remote"]}"\n'
            'submitted_at: ""\n',
            encoding="utf-8",
        )

    meta = parse_simple_yaml(meta_p.read_text(encoding="utf-8"))
    if meta.get("week") and meta.get("week") != week:
        errors.append(f"meta.yaml 中 week='{meta.get('week')}' 与目标周次 '{week}' 不一致")
    req = meta.get("meeting_request", "none") or "none"
    if req not in VALID_MEETING_REQUESTS:
        errors.append(f"meta.yaml 中 meeting_request='{req}' 无效，须为 {sorted(VALID_MEETING_REQUESTS)} 之一")

    if require_human:
        ev_p, jd_p = wdir / "evidence.md", wdir / "judgment.md"
        if not ev_p.is_file() or not jd_p.is_file():
            errors.append("当前协议要求人机分离：需同时存在 evidence.md 与 judgment.md")
            return errors, warnings
        for lbl, p, min_len in (("evidence.md", ev_p, 80), ("judgment.md", jd_p, 40)):
            txt = p.read_text(encoding="utf-8")
            for ph in UNFILLED_PLACEHOLDERS:
                if ph in txt:
                    errors.append(f"{lbl} 仍含未替换占位符：{ph}")
            body = strip_markdown_chrome(txt)
            if len(body) < min_len:
                errors.append(f"{lbl} 实质正文过短（{len(body)} < {min_len} 字）")
    else:
        rep_p = wdir / "report.md"
        ev_p = wdir / "evidence.md"
        target_p = rep_p if rep_p.is_file() else ev_p
        if not target_p.is_file():
            errors.append(f"缺少周报正文：{rep_p.relative_to(repo_dir)}")
            return errors, warnings
        txt = target_p.read_text(encoding="utf-8")
        for ph in UNFILLED_PLACEHOLDERS:
            if ph in txt:
                errors.append(f"{target_p.name} 仍含未替换占位符：{ph}")
        if "失败" not in txt:
            errors.append(f"{target_p.name} 需包含‘失败的尝试与卡点’记录（如实写负结果或尝试边界）")
        body = strip_markdown_chrome(txt)
        min_rep = int(limits.get("min_report_chars", 80))
        if len(body) < min_rep:
            errors.append(f"{target_p.name} 实质正文过短（{len(body)} < {min_rep} 字）")
        max_rep = int(limits.get("max_report_chars", 6000))
        if len(txt) > max_rep:
            warnings.append(f"{target_p.name} 篇幅较长 ({len(txt)} > {max_rep} 字符)，建议精简在 2 页内")

    # Stamp submitted_at if empty
    lines = meta_p.read_text(encoding="utf-8").splitlines()
    now_str = now_shanghai().isoformat(timespec="seconds")
    new_lines: List[str] = []
    found_sub = False
    for line in lines:
        if line.strip().startswith("submitted_at:"):
            found_sub = True
            val = line.split(":", 1)[1].strip().strip('"\'')
            new_lines.append(f'submitted_at: "{val or now_str}"')
        else:
            new_lines.append(line)
    if not found_sub:
        new_lines.append(f'submitted_at: "{now_str}"')
    meta_p.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return errors, warnings


def cmd_push(args: argparse.Namespace) -> int:
    hub_dir = Path(args.hub_dir).expanduser().resolve() if args.hub_dir else default_hub_dir()
    repo_dir = Path(args.repo_dir).expanduser().resolve()
    if not repo_dir.is_dir() or not (repo_dir / ".git").exists():
        print(f"[error] 私有库尚未初始化：{repo_dir}，请先运行 yao_sync.py init", file=sys.stderr)
        return 1

    maybe_pull_hub(hub_dir, force_pull=False, skip_remote=args.skip_remote)
    version_cfg, schedule = load_hub_configs(hub_dir)
    limits = version_cfg.get("limits", {})
    identity = detect_identity(skip_remote=True)
    cwd_info = detect_cwd_repo(Path.cwd(), repo_dir, hub_dir)
    migrate_notebook_schema(repo_dir, hub_dir, version_cfg, identity, cwd_info)

    dt = now_shanghai(args.now)
    sched_res = evaluate_schedule(schedule, dt, repo_dir)
    week = args.week or sched_res["active_week"]

    if not args.skip_remote:
        rem = run_cmd(["git", "remote", "get-url", "origin"], cwd=repo_dir, check=False)
        if rem.returncode == 0 and rem.stdout.strip():
            slug = parse_github_slug(rem.stdout.strip())
            if slug:
                verify_repo_not_public(slug)

    max_bytes = int(limits.get("max_single_file_bytes", MAX_SINGLE_FILE_BYTES))
    sec_errs = scan_secrets_and_sizes(repo_dir, max_bytes)
    if sec_errs:
        for e in sec_errs:
            print(f"[BLOCK] {e}", file=sys.stderr)
        return 3

    mode = args.mode
    if mode == "auto":
        wdir = repo_dir / "weekly" / week
        if (wdir / "report.md").is_file() or (wdir / "evidence.md").is_file():
            mode = "weekly"
        else:
            mode = "daily"

    if mode == "weekly":
        val_errs, val_warns = ensure_weekly_meta_and_validate(
            repo_dir, week, sched_res["require_human_judgment"], limits, identity, cwd_info
        )
        for w in val_warns:
            print(f"[warn] {w}")
        if val_errs:
            for e in val_errs:
                print(f"[BLOCK] {e}", file=sys.stderr)
            return 4

    if args.dry_run:
        print(f"[dry-run] 全部安全与格式门禁通过 (mode={mode}, week={week})。")
        return 0

    run_cmd(["git", "add", "-A"], cwd=repo_dir)
    st = run_cmd(["git", "status", "--porcelain"], cwd=repo_dir).stdout.strip()
    if st:
        msg = args.message or (f"weekly({week}): sync report" if mode == "weekly" else f"log({dt.date().isoformat()}): sync research note")
        run_cmd(["git", "commit", "-m", msg], cwd=repo_dir)
        print(f"[ok] 已提交本地记录：{msg}")
    else:
        print("[ok] 工作区无新改动。")

    if not args.skip_remote:
        rem = run_cmd(["git", "remote", "get-url", "origin"], cwd=repo_dir, check=False)
        if rem.returncode == 0 and rem.stdout.strip():
            branch = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir, check=False).stdout.strip() or "main"
            if branch == "HEAD":
                branch = "main"
            try:
                ls_rem = run_cmd(["git", "ls-remote", "--heads", "origin", branch], cwd=repo_dir, check=False, timeout=6)
                if ls_rem.returncode == 0 and ls_rem.stdout.strip():
                    run_cmd(["git", "pull", "--rebase", "--autostash", "origin", branch], cwd=repo_dir, check=False, timeout=10)
                push_p = run_cmd(["git", "push", "-u", "origin", branch], cwd=repo_dir, check=False, timeout=10)
                if push_p.returncode == 0:
                    print(f"[ok] 已推送至私有仓库 origin/{branch}")
                else:
                    print(f"[queued-offline] 远端推送暂未成功（{push_p.stderr.strip()[:120]}），已保留本地提交，下次同步自动补推。")
            except Exception as exc:
                print(f"[queued-offline] 网络超时或暂不可用（{exc}），已保留本地提交，下次同步自动补推。")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    return cmd_preflight(argparse.Namespace(
        hub_dir=args.hub_dir,
        repo_dir=args.repo_dir,
        force_pull=False,
        skip_remote=args.skip_remote,
        json_out=args.json_out,
        now=args.now,
    ))


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Yao Research Group 个人私有科研笔记与周报同步工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="初始化私有库与指定项目/全局 Agent 规则")
    p_init.add_argument("--project-dir", default=None, help="指定只装入某一个科研工作目录（推荐）")
    p_init.add_argument("--global", dest="use_global", action="store_true", help="显式确认写入全局 Agent 规则")
    p_init.add_argument("--targets", default=None, help="显式指定全局目标（claude,codex,gemini,cursor）")
    p_init.add_argument("--no-agent-md", action="store_true", help="跳过修改 AGENTS.md/CLAUDE.md")
    p_init.add_argument("--repo-name", default=None, help="私有仓库名（默认 yao-research-memory）")
    p_init.add_argument("--repo-dir", default="~/.yao-research/repo", help="本地私有库路径")
    p_init.add_argument("--hub-dir", default=None, help="本地协议中心路径")
    p_init.add_argument("--skip-remote", action="store_true", help="跳过远端 GitHub 建库")
    p_init.add_argument("--dry-run", action="store_true", help="仅预览操作不写文件")
    p_init.add_argument("--now", default=None, help=argparse.SUPPRESS)

    p_pre = sub.add_parser("preflight", help="自动热更新协议中心并检查当前周报排程与批示")
    p_pre.add_argument("--repo-dir", default="~/.yao-research/repo")
    p_pre.add_argument("--hub-dir", default=None)
    p_pre.add_argument("--force-pull", action="store_true")
    p_pre.add_argument("--skip-remote", action="store_true")
    p_pre.add_argument("--json", dest="json_out", action="store_true")
    p_pre.add_argument("--now", default=None, help=argparse.SUPPRESS)

    p_push = sub.add_parser("push", help="过隐私/安全门禁并同步本地科研笔记或周报")
    p_push.add_argument("--repo-dir", default="~/.yao-research/repo")
    p_push.add_argument("--hub-dir", default=None)
    p_push.add_argument("--mode", default="auto", choices=["auto", "daily", "weekly"])
    p_push.add_argument("--week", default=None)
    p_push.add_argument("--message", default=None)
    p_push.add_argument("--skip-remote", action="store_true")
    p_push.add_argument("--dry-run", action="store_true")
    p_push.add_argument("--now", default=None, help=argparse.SUPPRESS)

    p_st = sub.add_parser("status", help="查看本地私有库与当前周报排程状态")
    p_st.add_argument("--repo-dir", default="~/.yao-research/repo")
    p_st.add_argument("--hub-dir", default=None)
    p_st.add_argument("--skip-remote", action="store_true")
    p_st.add_argument("--json", dest="json_out", action="store_true")
    p_st.add_argument("--now", default=None, help=argparse.SUPPRESS)

    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if args.command == "init":
        return cmd_init(args)
    if args.command == "preflight":
        return cmd_preflight(args)
    if args.command == "push":
        return cmd_push(args)
    if args.command == "status":
        return cmd_status(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
