#!/usr/bin/env python3
"""Yao Research Group privacy-first manual progress sync CLI (v2.0).

Zero third-party dependencies (Python 3.9+ stdlib only). All I/O uses UTF-8.
Commands:
  init          Register an explicit project directory locally, create/bind private repo
                with pull-only collaborators, and install Git pre-commit privacy hook.
                Does NOT modify any global or project AGENTS.md / CLAUDE.md files.
  preview       Verify directory allowlist, scan pending changes, and print full plaintext.
  push          Require --confirm after preview; run allowlist + privacy gates + hook + push.
  status        Read-only summary of local policy, protocol version, schedule, and queue.
  check-update  Read-only fetch to inspect updates on hub without modifying local scripts.
  upgrade       Explicitly fast-forward local hub clone and refresh repo pre-commit hook.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import getpass
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

TZ_SHANGHAI = timezone(timedelta(hours=8))
DEFAULT_HUB_REPO = "Yao-Research-Group/research-hub"
DEFAULT_REPO_NAME = "yao-research-memory"
MAX_SINGLE_FILE_BYTES = 65536  # 64 KiB

ALLOWED_PATH_PATTERNS = [
    re.compile(r"^README\.md$"),
    re.compile(r"^\.gitignore$"),
    re.compile(r"^profile\.md$"),
    re.compile(r"^progress/\.gitkeep$"),
    re.compile(r"^weekly/\.gitkeep$"),
    re.compile(r"^progress/\d{4}-\d{2}-\d{2}\.md$"),
    re.compile(r"^weekly/\d{4}-W\d{2}/(?:meta\.yaml|report\.md|evidence\.md|judgment\.md)$"),
]

EXTERNAL_PATH_PATTERN = re.compile(
    r"(?:/Users/|/home/|/Volumes/|~/(?:OneDrive|Desktop|Documents|Downloads|Library|dev|Projects)\b"
    r"|[A-Za-z]:\\Users\\|\bOneDrive\b)",
    re.IGNORECASE,
)

SECRET_PATTERN = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----"
    r"|\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"
    r"|\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}"
    r"|\bBearer\s+[A-Za-z0-9._~-]{20,}"
    r"|https?://[^\s<>]*[?&#](?:[\w-]*token|api[-_]?key|key|secret|password|"
    r"[\w-]*signature|sig|auth)=[^\s&#<>]+",
    re.IGNORECASE,
)

EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
CN_PHONE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
VALID_MEETING_REQUESTS = {"none", "group", "1on1", "all"}

UNFILLED_PLACEHOLDERS = [
    "<用 1–3 句写清动作",
    "<如实记录未成功之处",
    "<下一项具体推进内容>",
    "<概括本周最实质的推进或核心判断，附来源>",
    "<实验协议、对照基线与真实数字",
    "<必填：本周跑不通、负提升、被证伪的做法",
    "<请用本人语言填写>",
    "<请写明核对了哪组数字或代码>",
]

STUDENT_GITIGNORE = """# Secrets, policies & personal local files
.env
.env.*
*.key
*.pem
*.p12
*.pfx
secrets/
*.local.json
local-policy.json

# Source code, datasets, binaries & weights (never sync to progress repo)
*.pt
*.pth
*.ckpt
*.safetensors
*.onnx
*.bin
*.parquet
*.feather
*.csv
*.tsv
*.xlsx
*.h5
*.hdf5
*.pdf
*.png
*.jpg
*.jpeg
*.tar.gz
*.zip

# OS & caches
.DS_Store
Thumbs.db
__pycache__/
*.pyc
.venv/
"""

PRE_COMMIT_HOOK_SCRIPT = '''#!/usr/bin/env python3
"""Native Git pre-commit privacy guard installed by Yao Research Hub (v2.0)."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys

MAX_BYTES = 65536
ALLOWED_PATHS = [
    re.compile(r"^README\\.md$"),
    re.compile(r"^\\.gitignore$"),
    re.compile(r"^profile\\.md$"),
    re.compile(r"^progress/\\.gitkeep$"),
    re.compile(r"^weekly/\\.gitkeep$"),
    re.compile(r"^progress/\\d{4}-\\d{2}-\\d{2}\\.md$"),
    re.compile(r"^weekly/\\d{4}-W\\d{2}/(?:meta\\.yaml|report\\.md|evidence\\.md|judgment\\.md)$"),
]
EXT_PATH_RE = re.compile(
    r"(?:/Users/|/home/|/Volumes/|~/(?:OneDrive|Desktop|Documents|Downloads|Library|dev|Projects)\\b"
    r"|[A-Za-z]:\\\\Users\\\\|\\bOneDrive\\b)",
    re.IGNORECASE,
)
SECRET_RE = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----"
    r"|\\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\\b"
    r"|\\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}"
    r"|\\bBearer\\s+[A-Za-z0-9._~-]{20,}"
    r"|https?://[^\\s<>]*[?&#](?:[\\w-]*token|api[-_]?key|key|secret|password|"
    r"[\\w-]*signature|sig|auth)=[^\\s&#<>]+",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"\\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}\\b")
PHONE_RE = re.compile(r"(?<!\\d)1[3-9]\\d{9}(?!\\d)")


def main() -> int:
    proc = subprocess.run(
        ["git", "-c", "core.quotePath=false", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if proc.returncode != 0:
        print("[PRE-COMMIT BLOCK] 无法获取暂存变更列表。", file=sys.stderr)
        return 1
    staged = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not staged:
        return 0

    errors = []
    for rel in staged:
        norm = rel.replace("\\\\", "/")
        if not any(p.match(norm) for p in ALLOWED_PATHS):
            errors.append(f"非白名单路径禁止提交（只允许今日进展与周报摘要）：{norm}")
            continue
        work_path = Path(rel)
        if work_path.is_symlink():
            errors.append(f"禁止提交符号链接：{norm}")
            continue
        blob = subprocess.run(["git", "cat-file", "-p", f":{rel}"], capture_output=True)
        if blob.returncode != 0:
            errors.append(f"无法读取暂存内容：{norm}")
            continue
        data = blob.stdout
        if len(data) > MAX_BYTES:
            errors.append(f"文件超出 64 KiB 上限 ({len(data)} > {MAX_BYTES} bytes)：{norm}")
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(f"禁止提交非 UTF-8 二进制文件：{norm}")
            continue
        if "\\x00" in text:
            errors.append(f"检测到空字节/二进制内容：{norm}")
            continue
        for idx, line in enumerate(text.splitlines(), start=1):
            if EXT_PATH_RE.search(line):
                errors.append(f"检测到本机外部路径或敏感目录引用：{norm}:{idx}")
            if SECRET_RE.search(line):
                errors.append(f"检测到疑似密钥/Token/带签名链接：{norm}:{idx}")
            for em in EMAIL_RE.findall(line):
                if not em.lower().endswith("@users.noreply.github.com"):
                    errors.append(f"检测到邮箱地址（PII 保护拦截）：{norm}:{idx}")
            if PHONE_RE.search(line):
                errors.append(f"检测到疑似 11 位手机号（PII 保护拦截）：{norm}:{idx}")

    if errors:
        for e in errors:
            print(f"[PRE-COMMIT BLOCK] {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


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


def default_policy_file() -> Path:
    return Path("~/.yao-research/local-policy.json").expanduser().resolve()


def load_hub_configs(hub_dir: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    version_path = hub_dir / "protocol" / "version.json"
    schedule_path = hub_dir / "protocol" / "schedule.json"
    version = json.loads(version_path.read_text(encoding="utf-8")) if version_path.is_file() else {}
    schedule = json.loads(schedule_path.read_text(encoding="utf-8")) if schedule_path.is_file() else {}
    return version, schedule


def load_policy(policy_file: Path) -> Dict[str, Any]:
    if not policy_file.is_file():
        return {}
    return json.loads(policy_file.read_text(encoding="utf-8"))


def save_policy(policy_file: Path, data: Dict[str, Any]) -> None:
    policy_file.parent.mkdir(parents=True, exist_ok=True)
    policy_file.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(policy_file, 0o600)
    except OSError:
        pass


def is_allowed_path_under_dirs(target: Path, allowed_dirs: List[str]) -> bool:
    t_str = str(target.resolve())
    for raw in allowed_dirs:
        a_str = str(Path(raw).expanduser().resolve())
        try:
            if os.path.commonpath([t_str, a_str]) == a_str:
                return True
        except ValueError:
            continue
    return False


def assert_allowed_cwd(policy: Dict[str, Any], from_dir: Optional[str]) -> Tuple[bool, str]:
    target = Path(from_dir).expanduser().resolve() if from_dir else Path.cwd().resolve()
    allowed = policy.get("allowed_project_dirs", [])
    if not allowed:
        return False, "未找到本地授权科研目录白名单（local-policy.json），请先运行 init --project-dir \"<科研目录>\"。"
    if not is_allowed_path_under_dirs(target, allowed):
        return False, (
            f"目录未授权：{target} 不在允许的科研工作目录白名单内（允许列表：{', '.join(allowed)}）。"
            "仅允许在白名单目录内触发预览或上传，如需新增目录请先显式执行 init --project-dir。"
        )
    return True, str(target)


def detect_github_login(skip_remote: bool) -> str:
    if not skip_remote and shutil.which("gh"):
        u = run_cmd(["gh", "api", "user", "--jq", ".login"], check=False, timeout=8)
        if u.returncode == 0 and u.stdout.strip():
            return u.stdout.strip()
    try:
        return getpass.getuser()
    except Exception:
        return "student"


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


def print_manual_github_guide(repo_name: str, repo_dir: Path, project_dir: Path, collaborators: List[Dict[str, str]]) -> None:
    collab_names = ", ".join(c["github"] for c in collaborators) or "（无外部协作者）"
    print(
        "\n[需要登录 gh 或按 3 步在网页建私有库]\n"
        "  1) 若有 gh，运行 gh auth login 后重跑本命令；\n"
        f"  2) 或打开 https://github.com/new ，仓库名填 {repo_name}，可见性必须选【Private】，\n"
        f"     然后在 Settings -> Collaborators 添加协作者（组织库选 Read/Pull；个人私有库直接 Add 即可）：{collab_names}；\n"
        f'  3) 执行：python3 ~/.yao-research/hub/scripts/yao_sync.py init --project-dir "{project_dir}" --skip-remote\n'
        f"     cd {repo_dir} && git remote add origin https://github.com/<你的GitHub账号>/{repo_name}.git && git push -u origin main\n",
        file=sys.stderr,
    )


def parse_github_slug(remote_url: str) -> Optional[str]:
    m = re.search(r"github\.com[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?$", remote_url.strip())
    return m.group(1) if m else None


def verify_repo_not_public(full_name: str) -> None:
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

    url = f"https://api.github.com/repos/{full_name}"
    req = urllib.request.Request(url, headers={"User-Agent": "yao-research-hub/2.0"})
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            meta = json.loads(resp.read().decode("utf-8"))
            if not meta.get("private", False):
                raise RuntimeError(f"安全拦截：远端仓库 {full_name} 可被匿名公开访问（PUBLIC）！已阻止写入。")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return
    except Exception:
        pass


def resolve_collaborators(
    default_collaborators: List[Dict[str, str]],
    collaborators_arg: Optional[str],
) -> List[Dict[str, str]]:
    if collaborators_arg is None:
        return list(default_collaborators)
    known = {item["github"].lower(): dict(item) for item in default_collaborators}
    resolved: List[Dict[str, str]] = []
    seen = set()
    for raw in collaborators_arg.split(","):
        login = raw.strip().lstrip("@")
        if not login or login.lower() in seen:
            continue
        seen.add(login.lower())
        if login.lower() in known:
            resolved.append(known[login.lower()])
        else:
            resolved.append({"github": login, "role": "collaborator", "permission": "pull"})
    return resolved


def ensure_private_remote_repo(
    owner: str,
    repo_name: str,
    collaborators: List[Dict[str, str]],
    existing_origin_slug: Optional[str],
    dry_run: bool,
) -> str:
    full_name = existing_origin_slug or f"{owner}/{repo_name}"
    if dry_run:
        collab_list = ", ".join(c["github"] for c in collaborators) or "none"
        print(f"[dry-run] Would ensure private repo {full_name} with collaborators: {collab_list}.")
        return full_name

    probe = run_cmd(["gh", "api", f"repos/{full_name}"], check=False)
    if probe.returncode == 0:
        meta = json.loads(probe.stdout)
        if not meta.get("private", False):
            raise RuntimeError(f"安全拦截：仓库 {full_name} 为 PUBLIC，禁止写入科研进展。")
        print(f"[ok] 复用已有私有仓库：{full_name}")
    else:
        desc = "Yao Research Group 个人私有科研进展仓库（仅纯文本进展摘要，无源码数据）"
        run_cmd(["gh", "repo", "create", full_name, "--private", "--description", desc])
        verify = json.loads(run_cmd(["gh", "api", f"repos/{full_name}"]).stdout)
        if not verify.get("private", False):
            raise RuntimeError(f"安全拦截：新建仓库 {full_name} 未处于 Private 状态！")
        print(f"[ok] 已创建私有仓库：{full_name}")

    repo_owner = full_name.split("/")[0]
    for item in collaborators:
        collab = item["github"]
        perm = item.get("permission", "pull")
        if collab.lower() == repo_owner.lower():
            continue
        proc = run_cmd(
            ["gh", "api", "-X", "PUT", f"repos/{full_name}/collaborators/{collab}", "-f", f"permission={perm}"],
            check=False,
        )
        if proc.returncode == 0:
            print(f"[ok] 已设置协作者权限：{collab} ({perm} 只读)")
            continue
        err_text = f"{proc.stderr}\n{proc.stdout}".strip()
        if ("422" in err_text) or ("Cannot assign" in err_text) or ("permission of read" in err_text):
            fallback = run_cmd(
                ["gh", "api", "-X", "PUT", f"repos/{full_name}/collaborators/{collab}"],
                check=False,
            )
            if fallback.returncode == 0:
                print(
                    f"[ok] 已邀请协作者：{collab}（注：GitHub 个人私有库平台层不支持只读 pull，"
                    "已按标准协作者邀请；汇总脚本 secretary_collect.py 为纯只读，本人可随时在 GitHub Settings 撤销）"
                )
                continue
            err_text = f"{fallback.stderr}\n{fallback.stdout}".strip()
        raise RuntimeError(f"授权协作者 {collab} 失败：{err_text}")
    return full_name


def install_pre_commit_hook(repo_dir: Path, dry_run: bool) -> Path:
    hook_path = repo_dir / ".git" / "hooks" / "pre-commit"
    if dry_run:
        print(f"[dry-run] Would install pre-commit privacy hook at {hook_path}")
        return hook_path
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    hook_path.write_text(PRE_COMMIT_HOOK_SCRIPT, encoding="utf-8")
    os.chmod(hook_path, 0o755)
    return hook_path


def write_if_missing(path: Path, content: str, dry_run: bool) -> bool:
    if path.exists():
        return False
    if dry_run:
        print(f"[dry-run] Would create {path}")
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def is_allowed_repo_rel_path(rel_path: str) -> bool:
    norm = rel_path.replace("\\", "/")
    return any(p.match(norm) for p in ALLOWED_PATH_PATTERNS)


def collect_pending_rel_paths(repo_dir: Path) -> List[str]:
    """Return sorted relative paths of staged, modified tracked, or untracked non-ignored files."""
    if not (repo_dir / ".git").exists():
        return []
    pending = set()
    cmds = [
        ["git", "-c", "core.quotePath=false", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        ["git", "-c", "core.quotePath=false", "diff", "--name-only", "--diff-filter=ACM"],
        ["git", "-c", "core.quotePath=false", "ls-files", "--others", "--exclude-standard"],
    ]
    for cmd in cmds:
        proc = run_cmd(cmd, cwd=repo_dir, check=False)
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                val = line.strip()
                if val:
                    pending.add(val)
    return sorted(pending)


def audit_paths(repo_dir: Path, rel_paths: List[str], max_bytes: int = MAX_SINGLE_FILE_BYTES) -> List[str]:
    errors: List[str] = []
    for rel in rel_paths:
        norm = rel.replace("\\", "/")
        if not is_allowed_repo_rel_path(norm):
            errors.append(f"非白名单路径禁止上传（仅允许今日进展与周报摘要）：{norm}")
            continue
        p = repo_dir / rel
        if p.is_symlink():
            errors.append(f"禁止上传符号链接：{norm}")
            continue
        if not p.is_file():
            continue
        data = p.read_bytes()
        if len(data) > max_bytes:
            errors.append(f"文件超出 {max_bytes} bytes (64 KiB) 上限 ({len(data)} bytes)：{norm}")
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(f"禁止上传非 UTF-8 二进制文件：{norm}")
            continue
        if "\x00" in text:
            errors.append(f"检测到二进制/空字节内容：{norm}")
            continue
        for idx, line in enumerate(text.splitlines(), start=1):
            if EXTERNAL_PATH_PATTERN.search(line):
                errors.append(f"检测到本机绝对路径或敏感目录引用：{norm}:{idx}（请删除路径只写进展摘要）")
            if SECRET_PATTERN.search(line):
                errors.append(f"检测到疑似密钥/Token/带签名链接：{norm}:{idx}（已隐藏内容）")
            for em in EMAIL_PATTERN.findall(line):
                if not em.lower().endswith("@users.noreply.github.com"):
                    errors.append(f"检测到邮箱地址（PII 保护拦截）：{norm}:{idx}")
            if CN_PHONE_PATTERN.search(line):
                errors.append(f"检测到疑似 11 位手机号（PII 保护拦截）：{norm}:{idx}")
    return errors


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

    has_ev = ev_p.is_file() and len(strip_markdown_chrome(ev_p.read_text(encoding="utf-8"))) >= 60 and not any(
        tok in ev_p.read_text(encoding="utf-8") for tok in UNFILLED_PLACEHOLDERS
    )
    has_jd = jd_p.is_file() and len(strip_markdown_chrome(jd_p.read_text(encoding="utf-8"))) >= 40 and not any(
        tok in jd_p.read_text(encoding="utf-8") for tok in UNFILLED_PLACEHOLDERS
    )
    has_rep = report_p.is_file() and len(strip_markdown_chrome(report_p.read_text(encoding="utf-8"))) >= 60 and not any(
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
        hr = int(reg.get("deadline_hour", 19))
        mn = int(reg.get("deadline_minute", 0))
        before_h = int(reg.get("window_hours_before", 48))
        after_h = int(reg.get("grace_hours_after", 24))
        require_human = bool(reg.get("require_human_judgment", False))

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
    action = "WEEKLY_REMINDER" if (in_window and not submitted) else "NONE"
    return {
        "in_window": in_window,
        "window_reason": window_reason or "outside_window",
        "active_week": check_week,
        "deadline": deadline_str,
        "require_human_judgment": require_human,
        "submission_state": sub_state,
        "action": action,
    }


def ahead_commit_count(repo_dir: Path) -> int:
    if not (repo_dir / ".git").exists():
        return 0
    proc = run_cmd(["git", "rev-list", "--count", "@{u}..HEAD"], cwd=repo_dir, check=False)
    if proc.returncode == 0 and proc.stdout.strip().isdigit():
        return int(proc.stdout.strip())
    return 0


def format_plaintext_preview(repo_dir: Path, rel_paths: List[str]) -> str:
    if not rel_paths:
        return "(当前无新增或修改的待同步文件)"
    blocks: List[str] = []
    for rel in rel_paths:
        p = repo_dir / rel
        if not p.is_file():
            blocks.append(f"--- DELETE {rel} ---")
            continue
        size = p.stat().st_size
        text = p.read_text(encoding="utf-8", errors="replace").rstrip("\n")
        blocks.append(f"--- BEGIN {rel} ({size} bytes) ---\n{text}\n--- END {rel} ---")
    return "\n\n".join(blocks)


def ensure_weekly_meta_and_validate(
    repo_dir: Path,
    week: str,
    require_human: bool,
    limits: Dict[str, Any],
    github_login: str,
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
            f'student_name: "{github_login}"\n'
            f'github_login: "{github_login}"\n'
            'role: "phd"\n'
            'topic: "待补充课题"\n'
            'report_mode: "ai_mvp"\n'
            'meeting_request: "none"\n'
            'blockers_summary: "无"\n'
            'help_needed_from: "无"\n'
            'help_needed_what: "无"\n'
            'has_product_task: "no"\n'
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
        for lbl, p, min_len in (("evidence.md", ev_p, 60), ("judgment.md", jd_p, 40)):
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
        min_rep = int(limits.get("min_report_chars", 60))
        if len(body) < min_rep:
            errors.append(f"{target_p.name} 实质正文过短（{len(body)} < {min_rep} 字）")
        max_rep = int(limits.get("max_report_chars", 6000))
        if len(txt) > max_rep:
            warnings.append(f"{target_p.name} 篇幅较长 ({len(txt)} > {max_rep} 字符)，建议精简在 2 页内")

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


def cmd_init(args: argparse.Namespace) -> int:
    hub_dir = Path(args.hub_dir).expanduser().resolve() if args.hub_dir else default_hub_dir()
    repo_dir = Path(args.repo_dir).expanduser().resolve()
    policy_file = Path(args.policy_file).expanduser().resolve()

    if not args.project_dir:
        print(
            "[error] v2.0 必须显式指定一个由本人确认的科研工作目录：--project-dir \"<科研目录>\"。\n"
            "本工具绝不触碰任何全局 Agent 规则文件（AGENTS.md / CLAUDE.md）。",
            file=sys.stderr,
        )
        return 2

    project_dir = Path(args.project_dir).expanduser().resolve()
    if not project_dir.is_dir():
        print(f"[error] 指定的科研目录不存在：{project_dir}。请确认真实路径后重试。", file=sys.stderr)
        return 2

    version_cfg, _ = load_hub_configs(hub_dir)
    repo_name = args.repo_name or version_cfg.get("default_repo_name", DEFAULT_REPO_NAME)
    default_collaborators = version_cfg.get("required_collaborators", [
        {"github": "taoyao2005", "role": "advisor", "permission": "pull"},
        {"github": "treerobin06", "role": "secretary", "permission": "pull"},
    ])
    collaborators = resolve_collaborators(default_collaborators, getattr(args, "collaborators", None))

    existing_origin_slug: Optional[str] = None
    if (repo_dir / ".git").exists():
        rem = run_cmd(["git", "remote", "get-url", "origin"], cwd=repo_dir, check=False)
        if rem.returncode == 0 and rem.stdout.strip():
            existing_origin_slug = parse_github_slug(rem.stdout.strip())

    owner = detect_github_login(skip_remote=args.skip_remote)
    full_repo_name = existing_origin_slug or f"{owner}/{repo_name}"

    if not args.skip_remote:
        ok, gh_owner = check_gh_ready()
        if not ok:
            print(f"[error] {gh_owner}", file=sys.stderr)
            print_manual_github_guide(repo_name, repo_dir, project_dir, collaborators)
            return 2
        owner = gh_owner
        advisor_logins = {c.get("github", "").lower() for c in collaborators if c.get("role") == "advisor"}
        if owner.lower() in advisor_logins:
            print(
                f"[note] 检测到当前 GitHub 账号 {owner} 为导师角色：导师只需接收同学私有库的协作者邀请，通常无需安装学生端。"
            )
        full_repo_name = ensure_private_remote_repo(
            owner=owner,
            repo_name=repo_name,
            collaborators=collaborators,
            existing_origin_slug=existing_origin_slug,
            dry_run=args.dry_run,
        )
    else:
        print("[info] 已跳过远端 GitHub 操作（--skip-remote）。")

    if not args.dry_run:
        repo_dir.mkdir(parents=True, exist_ok=True)
        if not (repo_dir / ".git").exists():
            run_cmd(["git", "init", "-b", "main"], cwd=repo_dir)
        if not run_cmd(["git", "config", "user.name"], cwd=repo_dir, check=False).stdout.strip():
            run_cmd(["git", "config", "user.name", owner], cwd=repo_dir)
        if not run_cmd(["git", "config", "user.email"], cwd=repo_dir, check=False).stdout.strip():
            run_cmd(["git", "config", "user.email", f"{owner}@users.noreply.github.com"], cwd=repo_dir)

    write_if_missing(repo_dir / ".gitignore", STUDENT_GITIGNORE, args.dry_run)
    collab_label = "、".join(f"`{c['github']}`" for c in collaborators) or "仅本人"
    write_if_missing(
        repo_dir / "README.md",
        (
            f"# {owner} · 个人私有科研进展仓库\n\n"
            f"- 可见性：Private（仅本人与授权协作者 {collab_label} 可见；汇总脚本仅只读拉取）\n"
            "- 允许内容：仅 `progress/YYYY-MM-DD.md`（今日进展）与 `weekly/YYYY-Www/report.md`（纯文本摘要）\n"
        ),
        args.dry_run,
    )
    write_if_missing(
        repo_dir / "profile.md",
        (
            "# 成员档案（极简无遥测版）\n\n"
            f"- **GitHub ID**：{owner}\n"
            f"- **仓库地址**：{full_repo_name}\n"
            "- **称呼（选填）**：待补充\n"
            "- **主课题（选填）**：待补充\n"
        ),
        args.dry_run,
    )
    for sub in ("progress", "weekly"):
        write_if_missing(repo_dir / sub / ".gitkeep", "", args.dry_run)

    install_pre_commit_hook(repo_dir, args.dry_run)

    # Store allowlist locally ONLY (never inside repo_dir)
    policy = load_policy(policy_file)
    allowed_dirs = list(policy.get("allowed_project_dirs", []))
    proj_str = str(project_dir)
    if proj_str not in allowed_dirs:
        allowed_dirs.append(proj_str)
    policy.update(
        {
            "schema_version": version_cfg.get("schema_version", "2.0.0"),
            "repo_dir": str(repo_dir),
            "hub_dir": str(hub_dir),
            "github_login": owner,
            "repo": full_repo_name,
            "require_confirm": True,
            "allowed_project_dirs": allowed_dirs,
            "updated_at": now_shanghai().isoformat(timespec="seconds"),
        }
    )
    if not args.dry_run:
        save_policy(policy_file, policy)

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

    print(f"[done] v2.0 隐私受控初始化完成：私有库={full_repo_name}")
    print(f"[ok] 本地授权目录白名单：{policy_file} -> {proj_str}")
    print(f"[ok] 已安装原生 Git pre-commit 隐私硬钩子：{repo_dir / '.git' / 'hooks' / 'pre-commit'}")
    print("[info] 绝未修改任何全局或项目 AGENTS.md/CLAUDE.md，未开启自动上传。")
    print("[tip] 需要同步时请主动对 AI 说整理进展 -> 运行 preview 查看完整明文 -> 确认后运行 push --confirm。")
    print("[tip] 若希望定期提醒自己手动整理周报，可自选设立纯本地日程提醒（仅提醒本人，不自动上传）。")
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    hub_dir = Path(args.hub_dir).expanduser().resolve() if args.hub_dir else default_hub_dir()
    repo_dir = Path(args.repo_dir).expanduser().resolve()
    policy_file = Path(args.policy_file).expanduser().resolve()

    if not repo_dir.is_dir() or not (repo_dir / ".git").exists():
        print(f"[error] 私有库尚未初始化：{repo_dir}，请先运行 init --project-dir \"<科研目录>\"", file=sys.stderr)
        return 1

    policy = load_policy(policy_file)
    ok_cwd, cwd_msg = assert_allowed_cwd(policy, args.from_dir)
    if not ok_cwd:
        print(f"[BLOCK] {cwd_msg}", file=sys.stderr)
        return 5

    version_cfg, _ = load_hub_configs(hub_dir)
    max_bytes = int(version_cfg.get("limits", {}).get("max_single_file_bytes", MAX_SINGLE_FILE_BYTES))
    pending = collect_pending_rel_paths(repo_dir)
    if not pending:
        print(f"[preview] 目录白名单校验通过 ({cwd_msg})，当前没有待同步的新增或修改文件。")
        return 0

    errs = audit_paths(repo_dir, pending, max_bytes=max_bytes)
    if errs:
        for e in errs:
            print(f"[BLOCK] {e}", file=sys.stderr)
        return 3

    print(f"[preview] 授权目录校验通过：{cwd_msg}")
    print(f"[preview] 隐私扫描全部通过，本次拟同步 {len(pending)} 个文件，完整明文如下（请本人审阅）：\n")
    print(format_plaintext_preview(repo_dir, pending))
    print(f"\n[preview] 以上内容尚未提交或推送。确认无误后，请运行带 --confirm 的 push 命令上传。")
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    hub_dir = Path(args.hub_dir).expanduser().resolve() if args.hub_dir else default_hub_dir()
    repo_dir = Path(args.repo_dir).expanduser().resolve()
    policy_file = Path(args.policy_file).expanduser().resolve()

    if not repo_dir.is_dir() or not (repo_dir / ".git").exists():
        print(f"[error] 私有库尚未初始化：{repo_dir}，请先运行 init --project-dir \"<科研目录>\"", file=sys.stderr)
        return 1

    policy = load_policy(policy_file)
    ok_cwd, cwd_msg = assert_allowed_cwd(policy, args.from_dir)
    if not ok_cwd:
        print(f"[BLOCK] {cwd_msg}", file=sys.stderr)
        return 5

    version_cfg, schedule = load_hub_configs(hub_dir)
    limits = version_cfg.get("limits", {})
    max_bytes = int(limits.get("max_single_file_bytes", MAX_SINGLE_FILE_BYTES))
    dt = now_shanghai(args.now)
    sched_res = evaluate_schedule(schedule, dt, repo_dir)
    week = args.week or sched_res["active_week"]

    pending = collect_pending_rel_paths(repo_dir)
    errs = audit_paths(repo_dir, pending, max_bytes=max_bytes)
    if errs:
        for e in errs:
            print(f"[BLOCK] {e}", file=sys.stderr)
        return 3

    mode = args.mode
    if mode == "auto":
        if any(p.startswith(f"weekly/{week}/") for p in pending):
            mode = "weekly"
        else:
            mode = "progress"

    if mode == "weekly":
        github_login = policy.get("github_login") or detect_github_login(skip_remote=True)
        val_errs, val_warns = ensure_weekly_meta_and_validate(
            repo_dir, week, sched_res["require_human_judgment"], limits, github_login
        )
        for w in val_warns:
            print(f"[warn] {w}")
        if val_errs:
            for e in val_errs:
                print(f"[BLOCK] {e}", file=sys.stderr)
            return 4
        # Refresh pending list in case meta.yaml was generated/updated
        pending = collect_pending_rel_paths(repo_dir)
        errs2 = audit_paths(repo_dir, pending, max_bytes=max_bytes)
        if errs2:
            for e in errs2:
                print(f"[BLOCK] {e}", file=sys.stderr)
            return 3

    if not args.confirm:
        print("[BLOCK] 安全门禁：未传 --confirm，已阻止推送。请先向本人完整展示下方内容，获明确同意后再加 --confirm 执行：\n", file=sys.stderr)
        print(format_plaintext_preview(repo_dir, pending), file=sys.stderr)
        return 6

    if not args.skip_remote:
        rem = run_cmd(["git", "remote", "get-url", "origin"], cwd=repo_dir, check=False)
        if rem.returncode == 0 and rem.stdout.strip():
            slug = parse_github_slug(rem.stdout.strip())
            if slug:
                verify_repo_not_public(slug)

    if pending:
        run_cmd(["git", "add", "--"] + pending, cwd=repo_dir)
    st = run_cmd(["git", "status", "--porcelain"], cwd=repo_dir).stdout.strip()
    if st:
        msg = args.message or (f"weekly({week}): sync report" if mode == "weekly" else f"progress({dt.date().isoformat()}): sync progress")
        commit_p = run_cmd(["git", "commit", "-m", msg], cwd=repo_dir, check=False)
        if commit_p.returncode != 0:
            err_txt = commit_p.stderr.strip() or commit_p.stdout.strip()
            print(f"[BLOCK] 提交被拦截：{err_txt}", file=sys.stderr)
            return 3
        print(f"[ok] 已提交本地进展：{msg}（来源白名单：{cwd_msg}）")
    else:
        print("[ok] 工作区无新变更。")

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
                    print(f"[queued-offline] 远端推送暂未成功（{push_p.stderr.strip()[:120]}），已保留本地提交，下次确认推送时一并上传。")
            except Exception as exc:
                print(f"[queued-offline] 网络超时或暂不可用（{exc}），已保留本地提交，下次确认推送时一并上传。")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    hub_dir = Path(args.hub_dir).expanduser().resolve() if args.hub_dir else default_hub_dir()
    repo_dir = Path(args.repo_dir).expanduser().resolve()
    policy_file = Path(args.policy_file).expanduser().resolve()

    version_cfg, schedule = load_hub_configs(hub_dir)
    policy = load_policy(policy_file)
    ok_cwd, cwd_msg = assert_allowed_cwd(policy, getattr(args, "from_dir", None))
    dt = now_shanghai(args.now)
    sched_res = evaluate_schedule(schedule, dt, repo_dir)
    pending = collect_pending_rel_paths(repo_dir)
    ahead = ahead_commit_count(repo_dir)

    result = {
        "schema_version": version_cfg.get("schema_version", "2.0.0"),
        "hub_dir": str(hub_dir),
        "repo_dir": str(repo_dir),
        "policy_file": str(policy_file),
        "allowed_project_dirs": policy.get("allowed_project_dirs", []),
        "cwd_authorized": ok_cwd,
        "cwd_detail": cwd_msg,
        "pending_files": pending,
        "unpushed_commits": ahead,
        **sched_res,
    }
    if getattr(args, "json_out", False):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"[status] 协议版本: v{result['schema_version']} | 授权目录数: {len(result['allowed_project_dirs'])} | 当前目录授权: {ok_cwd}")
        print(f"[status] 本周({result['active_week']}) 状态: {result['submission_state']} | 窗口: {result['window_reason']} | 提醒: {result['action']}")
        print(f"[status] 待预览文件: {len(pending)} | 本地待推提交: {ahead}")
    return 0


def cmd_check_update(args: argparse.Namespace) -> int:
    hub_dir = Path(args.hub_dir).expanduser().resolve() if args.hub_dir else default_hub_dir()
    if not (hub_dir / ".git").exists():
        print(f"[error] {hub_dir} 不是 Git 仓库。", file=sys.stderr)
        return 1
    run_cmd(["git", "fetch", "origin"], cwd=hub_dir, timeout=10)
    log_p = run_cmd(["git", "log", "HEAD..origin/main", "--oneline", "--stat"], cwd=hub_dir, check=False)
    out = log_p.stdout.strip()
    if not out:
        print("[ok] 本地协议中心已是最新版本。")
    else:
        print("[update-available] 远端有以下更新（需本人确认后运行 upgrade）：\n" + out)
    return 0


def cmd_upgrade(args: argparse.Namespace) -> int:
    hub_dir = Path(args.hub_dir).expanduser().resolve() if args.hub_dir else default_hub_dir()
    repo_dir = Path(args.repo_dir).expanduser().resolve()
    if not (hub_dir / ".git").exists():
        print(f"[error] {hub_dir} 不是 Git 仓库。", file=sys.stderr)
        return 1
    run_cmd(["git", "pull", "--ff-only"], cwd=hub_dir, timeout=15)
    if (repo_dir / ".git").exists():
        install_pre_commit_hook(repo_dir, dry_run=False)
    print(f"[ok] 已手动升级协议中心：{hub_dir}")
    return 0


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Yao Research Group 隐私优先科研进展手动同步工具 (v2.0.1)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="登记授权科研目录、建私有库(支持 --collaborators)并装Git隐私硬钩子")
    p_init.add_argument("--project-dir", default=None, help="必填：用户明确指定的一个科研工作目录")
    p_init.add_argument("--repo-name", default=None, help="私有仓库名（默认 yao-research-memory）")
    p_init.add_argument("--repo-dir", default="~/.yao-research/repo", help="本地私有库路径")
    p_init.add_argument("--hub-dir", default=None, help="本地协议中心路径")
    p_init.add_argument("--policy-file", default=str(default_policy_file()), help="本地白名单策略文件")
    p_init.add_argument(
        "--collaborators",
        default=None,
        help="逗号分隔指定邀请的 GitHub 协作者，默认从 protocol/version.json 读取，如 taoyao2005",
    )
    p_init.add_argument("--skip-remote", action="store_true", help="跳过远端 GitHub 建库")
    p_init.add_argument("--dry-run", action="store_true", help="仅预览操作不写文件")

    p_prev = sub.add_parser("preview", help="校验白名单目录、扫描隐私风险并打印完整明文预览")
    p_prev.add_argument("--repo-dir", default="~/.yao-research/repo")
    p_prev.add_argument("--hub-dir", default=None)
    p_prev.add_argument("--policy-file", default=str(default_policy_file()))
    p_prev.add_argument("--from-dir", default=None, help="来源工作目录（默认当前 CWD）")

    p_push = sub.add_parser("push", help="过隐私硬门禁并在带 --confirm 时提交推送")
    p_push.add_argument("--confirm", action="store_true", help="必填确认标志：必须先经本人审阅预览明文后再传")
    p_push.add_argument("--repo-dir", default="~/.yao-research/repo")
    p_push.add_argument("--hub-dir", default=None)
    p_push.add_argument("--policy-file", default=str(default_policy_file()))
    p_push.add_argument("--from-dir", default=None, help="来源工作目录（默认当前 CWD）")
    p_push.add_argument("--mode", default="auto", choices=["auto", "progress", "weekly"])
    p_push.add_argument("--week", default=None)
    p_push.add_argument("--message", default=None)
    p_push.add_argument("--skip-remote", action="store_true")
    p_push.add_argument("--now", default=None, help=argparse.SUPPRESS)

    for cmd_name in ("status", "preflight"):
        p_st = sub.add_parser(cmd_name, help="只读查看授权目录、周报窗口与本地提交状态（绝不自动拉取代码）")
        p_st.add_argument("--repo-dir", default="~/.yao-research/repo")
        p_st.add_argument("--hub-dir", default=None)
        p_st.add_argument("--policy-file", default=str(default_policy_file()))
        p_st.add_argument("--from-dir", default=None)
        p_st.add_argument("--json", dest="json_out", action="store_true")
        p_st.add_argument("--now", default=None, help=argparse.SUPPRESS)

    p_cu = sub.add_parser("check-update", help="只读检查协议中心远端更新（不改动本地文件）")
    p_cu.add_argument("--hub-dir", default=None)

    p_up = sub.add_parser("upgrade", help="手动升级本地协议中心并刷新 pre-commit 钩子")
    p_up.add_argument("--hub-dir", default=None)
    p_up.add_argument("--repo-dir", default="~/.yao-research/repo")

    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if args.command == "init":
        return cmd_init(args)
    if args.command == "preview":
        return cmd_preview(args)
    if args.command == "push":
        return cmd_push(args)
    if args.command in ("status", "preflight"):
        return cmd_status(args)
    if args.command == "check-update":
        return cmd_check_update(args)
    if args.command == "upgrade":
        return cmd_upgrade(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
