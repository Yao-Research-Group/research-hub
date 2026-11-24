#!/usr/bin/env python3
"""Yao Research Group v3.0: directory-jailed seamless research sync CLI.

Zero third-party dependencies (Python 3.9+ stdlib only). All I/O uses UTF-8.

Key guarantees:
  - ZERO global AI-rule modification (never touches ~/.claude, ~/.codex, ~/.gemini, ~/.cursor).
  - Hard local directory jail (~/.yao-research/local-policy.json, chmod 0600).
  - Supports code (code/), experimental artifacts/figures (weekly/<W>/artifacts/),
    daily progress (progress/), dual-track weekly reports (evidence.md + judgment.md),
    and allowlist-scoped session summaries (sessions/).
  - Layered guardrails: Tier-1 hard blocks (jail escape, symlinks, secrets, keys, weights, >10MiB);
    Tier-2 soft warnings (broken local absolute paths in reports, leaked AI draft notes, PII).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import fnmatch
import getpass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
import urllib.error
import urllib.request

SCHEMA_VERSION = "3.0.0"
TZ_SHANGHAI = timezone(timedelta(hours=8))
DEFAULT_HUB_REPO = "Yao-Research-Group/research-hub"
DEFAULT_REPO_NAME = "yao-research-memory"
MAX_SINGLE_FILE_BYTES = 10 * 1024 * 1024  # 10 MiB
PREVIEW_TEXT_CHAR_LIMIT = 12000

MARKER_START = "<!-- BEGIN YAO-RESEARCH-HUB -->"
MARKER_END = "<!-- END YAO-RESEARCH-HUB -->"

ALLOWED_BINARY_ASSET_EXTS: Set[str] = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".pdf",
}

BLOCKED_EXTS: Set[str] = {
    ".pt",
    ".pth",
    ".ckpt",
    ".safetensors",
    ".onnx",
    ".gguf",
    ".h5",
    ".hdf5",
    ".parquet",
    ".feather",
    ".zip",
    ".tar",
    ".gz",
    ".tgz",
    ".7z",
    ".rar",
    ".mp4",
    ".mov",
    ".avi",
    ".db",
    ".sqlite",
    ".sqlite3",
}

BLOCKED_FILENAME_PATTERNS: Tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    "id_ecdsa*",
    "*cookie*",
    "*credential*",
    "*secret*",
    "all_keys.json",
    "local-policy.json",
    ".DS_Store",
    "Thumbs.db",
)

SKIP_DIR_NAMES: Set[str] = {
    ".git",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".ipynb_checkpoints",
    "wandb",
    "mlruns",
}

ALLOWED_PATH_PATTERNS = [
    re.compile(r"^README\.md$"),
    re.compile(r"^\.gitignore$"),
    re.compile(r"^profile\.md$"),
    re.compile(r"^(?:progress|weekly|code|sessions)/\.gitkeep$"),
    re.compile(r"^progress/\d{4}-\d{2}-\d{2}\.md$"),
    re.compile(r"^weekly/\d{4}-W\d{2}/(?:meta\.yaml|evidence\.md|judgment\.md|report\.md)$"),
    re.compile(r"^weekly/\d{4}-W\d{2}/artifacts/.+$"),
    re.compile(r"^code/.+$"),
    re.compile(r"^sessions/\d{4}-\d{2}-\d{2}[A-Za-z0-9._-]*\.md$"),
    re.compile(r"^projects/[A-Za-z0-9._-]+\.md$"),
]

REPORT_DOC_PATTERNS = [
    re.compile(r"^progress/\d{4}-\d{2}-\d{2}\.md$"),
    re.compile(r"^weekly/\d{4}-W\d{2}/(?:evidence|judgment|report)\.md$"),
    re.compile(r"^sessions/\d{4}-\d{2}-\d{2}[A-Za-z0-9._-]*\.md$"),
]

LOCAL_ABS_PATH_PATTERN = re.compile(
    r"(?:/Users/|/home/|/Volumes/|~/(?:OneDrive|Desktop|Documents|Downloads|Library|dev|Projects)\b"
    r"|[A-Za-z]:[/\\]|\bOneDrive\b)",
    re.IGNORECASE,
)

AI_DRAFT_LEAK_PATTERNS: Tuple[str, ...] = (
    "本地审阅草稿",
    "先生成 diff 并由用户确认",
    "请确认是否上传",
    "AI_DRAFT_PENDING_HUMAN",
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
VALID_MEETING_REQUESTS = {"none", "group", "1on1", "thu_review", "sat_product", "all"}

UNFILLED_PLACEHOLDERS = [
    "<用 1–3 句写清动作",
    "<如实记录未成功之处",
    "<下一项具体推进内容>",
    "<概括本周最实质的推进或核心判断，附来源>",
    "<实验协议、对照基线与真实数字",
    "<必填：本周跑不通、负提升、被证伪的做法",
    "<请用本人语言填写>",
    "<请写明核对了哪组数字或代码>",
    "<用一句话说清核心发现或真实卡点>",
    "<推翻或修正了什么旧假设>",
    "<写明具体交付物、目标日期与主动舍弃项>",
    "<写明亲自核对了哪组数字、代码或日志",
]

DEFAULT_ADVISOR_COLLABORATORS = [
    {"github": "taoyao2005", "role": "advisor", "permission": "pull"},
]

STUDENT_GITIGNORE = """# Secrets, policies & credentials (always ignored)
.env
.env.*
*.key
*.pem
*.p12
*.pfx
id_rsa*
id_ed25519*
*cookie*
*credential*
*secret*
secrets/
*.local.json
all_keys.json
local-policy.json

# Weights, large tabular dumps & archives (never commit)
*.pt
*.pth
*.ckpt
*.safetensors
*.onnx
*.gguf
*.h5
*.hdf5
*.parquet
*.feather
*.zip
*.tar
*.tar.gz
*.tgz
*.7z
*.rar
*.mp4
*.mov
*.avi
*.db
*.sqlite
*.sqlite3

# OS & environment caches
.DS_Store
Thumbs.db
__pycache__/
*.pyc
.venv/
venv/
node_modules/
wandb/
mlruns/
"""

PRE_COMMIT_HOOK_SCRIPT = '''#!/usr/bin/env python3
"""Native Git pre-commit Tier-1 guardrail installed by Yao Research Hub (v3.0)."""
from __future__ import annotations

import fnmatch
from pathlib import Path
import re
import subprocess
import sys

MAX_BYTES = 10 * 1024 * 1024  # 10 MiB

ALLOWED_PATHS = [
    re.compile(r"^README\\.md$"),
    re.compile(r"^\\.gitignore$"),
    re.compile(r"^profile\\.md$"),
    re.compile(r"^(?:progress|weekly|code|sessions)/\\.gitkeep$"),
    re.compile(r"^progress/\\d{4}-\\d{2}-\\d{2}\\.md$"),
    re.compile(r"^weekly/\\d{4}-W\\d{2}/(?:meta\\.yaml|evidence\\.md|judgment\\.md|report\\.md)$"),
    re.compile(r"^weekly/\\d{4}-W\\d{2}/artifacts/.+$"),
    re.compile(r"^code/.+$"),
    re.compile(r"^sessions/\\d{4}-\\d{2}-\\d{2}[A-Za-z0-9._-]*\\.md$"),
    re.compile(r"^projects/[A-Za-z0-9._-]+\\.md$"),
]

ALLOWED_BINARY_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf"}
BLOCKED_EXTS = {
    ".pt", ".pth", ".ckpt", ".safetensors", ".onnx", ".gguf",
    ".h5", ".hdf5", ".parquet", ".feather",
    ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar",
    ".mp4", ".mov", ".avi", ".db", ".sqlite", ".sqlite3",
}
BLOCKED_FILENAMES = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx",
    "id_rsa*", "id_ed25519*", "id_ecdsa*",
    "*cookie*", "*credential*", "*secret*",
    "all_keys.json", "local-policy.json", ".DS_Store", "Thumbs.db",
)
SKIP_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".ipynb_checkpoints",
    "wandb", "mlruns",
}

SECRET_RE = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----"
    r"|\\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\\b"
    r"|\\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}"
    r"|\\bBearer\\s+[A-Za-z0-9._~-]{20,}"
    r"|https?://[^\\s<>]*[?&#](?:[\\w-]*token|api[-_]?key|key|secret|password|"
    r"[\\w-]*signature|sig|auth)=[^\\s&#<>]+",
    re.IGNORECASE,
)


def is_blocked_name(norm_path: str) -> bool:
    parts = norm_path.split("/")
    if any(p in SKIP_DIRS for p in parts[:-1]):
        return True
    name = parts[-1]
    lower_name = name.lower()
    if any(fnmatch.fnmatch(lower_name, pat.lower()) for pat in BLOCKED_FILENAMES):
        return True
    ext = Path(name).suffix.lower()
    if ext in BLOCKED_EXTS or lower_name.endswith(".tar.gz"):
        return True
    return False


def allows_binary(norm_path: str) -> bool:
    ext = Path(norm_path).suffix.lower()
    if ext not in ALLOWED_BINARY_EXTS:
        return False
    return norm_path.startswith("code/") or "/artifacts/" in norm_path


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
            errors.append(f"非仓库白名单路径禁止提交：{norm}")
            continue
        if is_blocked_name(norm):
            errors.append(f"敏感文件、缓存目录或禁用大文件/权重类型禁止提交：{norm}")
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
            errors.append(f"文件超出 10 MiB 上限 ({len(data)} > {MAX_BYTES} bytes)：{norm}")
            continue
        if allows_binary(norm):
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(f"非白名单图表/PDF 的未知二进制文件禁止提交：{norm}")
            continue
        if "\\x00" in text:
            errors.append(f"检测到空字节/二进制内容：{norm}")
            continue
        for idx, line in enumerate(text.splitlines(), start=1):
            if SECRET_RE.search(line):
                errors.append(f"检测到疑似私钥/密钥/Token/带签名链接：{norm}:{idx}")

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
    input_bytes: Optional[bytes] = None,
) -> subprocess.CompletedProcess:
    if input_bytes is not None:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            input=input_bytes,
            capture_output=True,
            timeout=timeout,
        )
        stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
        stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
        res = subprocess.CompletedProcess(cmd, proc.returncode, stdout=stdout, stderr=stderr)
    else:
        res = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
        )
    if check and res.returncode != 0:
        msg = res.stderr.strip() or res.stdout.strip() or f"exit code {res.returncode}"
        raise RuntimeError(f"Command failed ({' '.join(cmd)}): {msg}")
    return res


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


def is_allowed_path_under_dirs(target: Path, allowed_dirs: Sequence[str]) -> bool:
    """Strictly verify target.resolve() stays inside at least one allowlisted root."""
    t_str = str(target.resolve(strict=False))
    for raw in allowed_dirs:
        a_str = str(Path(raw).expanduser().resolve(strict=False))
        try:
            if os.path.commonpath([t_str, a_str]) == a_str:
                return True
        except ValueError:
            continue
    return False


def assert_path_in_allowed_dirs(path: Path, allowed_dirs: Sequence[str]) -> Tuple[bool, str]:
    if not allowed_dirs:
        return False, "未找到本地授权科研目录白名单（local-policy.json），请先运行 init --project-dir \"<科研目录>\"。"
    resolved = path.resolve(strict=False)
    if not is_allowed_path_under_dirs(resolved, allowed_dirs):
        return False, (
            f"目录未授权：{resolved} 不在允许的科研工作目录白名单内（允许列表：{', '.join(allowed_dirs)}）。"
            "仅允许同步白名单目录内的文件；如需新增目录请先显式执行 init --project-dir。"
        )
    return True, str(resolved)


def assert_allowed_cwd(policy: Dict[str, Any], from_dir: Optional[str]) -> Tuple[bool, str]:
    target = Path(from_dir).expanduser().resolve() if from_dir else Path.cwd().resolve()
    return assert_path_in_allowed_dirs(target, policy.get("allowed_project_dirs", []))


def find_matching_allowed_root(path: Path, allowed_dirs: Sequence[str]) -> Optional[Path]:
    t_str = str(path.resolve(strict=False))
    best: Optional[Path] = None
    best_len = -1
    for raw in allowed_dirs:
        root = Path(raw).expanduser().resolve(strict=False)
        r_str = str(root)
        try:
            if os.path.commonpath([t_str, r_str]) == r_str and len(r_str) > best_len:
                best = root
                best_len = len(r_str)
        except ValueError:
            continue
    return best


def is_blocked_filename_or_ext(rel_path: str) -> bool:
    norm = rel_path.replace("\\", "/")
    parts = [p for p in norm.split("/") if p]
    if not parts:
        return True
    if any(p in SKIP_DIR_NAMES for p in parts[:-1]):
        return True
    name = parts[-1]
    lower_name = name.lower()
    if any(fnmatch.fnmatch(lower_name, pat.lower()) for pat in BLOCKED_FILENAME_PATTERNS):
        return True
    ext = Path(name).suffix.lower()
    if ext in BLOCKED_EXTS or lower_name.endswith(".tar.gz"):
        return True
    return False


def is_allowed_binary_asset(rel_path: str) -> bool:
    norm = rel_path.replace("\\", "/")
    ext = Path(norm).suffix.lower()
    if ext not in ALLOWED_BINARY_ASSET_EXTS:
        return False
    return norm.startswith("code/") or "/artifacts/" in norm


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


def print_manual_github_guide(
    repo_name: str,
    repo_dir: Path,
    project_dir: Path,
    collaborators: List[Dict[str, str]],
) -> None:
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
    req = urllib.request.Request(url, headers={"User-Agent": "yao-research-hub/3.0"})
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
    seen: Set[str] = set()
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
            raise RuntimeError(f"安全拦截：仓库 {full_name} 为 PUBLIC，禁止写入科研资料。")
        print(f"[ok] 复用已有私有仓库：{full_name}")
    else:
        desc = "Yao Research Group 个人私有科研仓库（仅授权导师/队友可见，含进展/周报/可审计代码图表）"
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
                    "已按标准协作者邀请；汇总脚本 advisor_collect.py 为纯只读，本人可随时在 GitHub Settings 撤销）"
                )
                continue
            err_text = f"{fallback.stderr}\n{fallback.stdout}".strip()
        raise RuntimeError(f"授权协作者 {collab} 失败：{err_text}")
    return full_name


def install_pre_commit_hook(repo_dir: Path, dry_run: bool) -> Path:
    hook_path = repo_dir / ".git" / "hooks" / "pre-commit"
    if dry_run:
        print(f"[dry-run] Would install pre-commit guardrail at {hook_path}")
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


def build_project_rule_block(
    project_dir: Path,
    repo_dir: Path,
    hub_dir: Path,
    confirm_mode: str,
) -> str:
    if confirm_mode == "auto-in-allowlist":
        confirm_note = (
            "本目录已启用白名单内无感同步：在完成本目录及其子目录下的科研推进、代码/图表产物整理或项目讨论后，"
            "可自动同步至 `progress/YYYY-MM-DD.md`、`code/`、`weekly/<W>/artifacts/` 与脱敏的 `sessions/YYYY-MM-DD-*.md`，"
            "并直接运行 `push` / `upload`；"
            "**例外**：修改或创建学生本人判断页 `weekly/<W>/judgment.md` 时，必须先完整展示给本人审阅并加 `--confirm`。"
        )
    else:
        confirm_note = (
            "当前为预览确认模式：整理本目录及其子目录的进展、`evidence.md`、代码/图表或 `sessions/YYYY-MM-DD-*.md` 后，"
            "先运行 `preview` 向本人展示清单与摘要，获得明确同意后再加 `--confirm` 执行 `push` 或 `upload`。"
        )
    return "\n".join(
        [
            MARKER_START,
            f"## 课题组科研同步（仅限本白名单目录：`{project_dir.name}`）",
            "",
            f"- 协议中心：`{hub_dir}`（见 `protocol/CURRENT.md`）｜私有仓库：`{repo_dir}`",
            f"- **硬范围与零全局影响**：只适用于本目录及其子目录内的文件、代码、图表与科研讨论项目；绝不影响本机其他目录的 AI 对话，绝不读取本目录之外的内容，也不写入密钥、大表或模型权重到 `{repo_dir}`。",
            f"- **触发与上传**：{confirm_note}",
            f"- 快捷上传：`python3 \"{hub_dir / 'scripts' / 'yao_sync.py'}\" upload --from-dir \"{project_dir}\" ...`",
            MARKER_END,
        ]
    )


def _upsert_marked_block(target_file: Path, block: str, dry_run: bool) -> bool:
    if dry_run:
        print(f"[dry-run] Would update project rule block in {target_file}")
        return True
    if target_file.is_file():
        orig = target_file.read_text(encoding="utf-8")
        pattern = re.compile(
            re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END),
            flags=re.DOTALL,
        )
        if pattern.search(orig):
            updated = pattern.sub(block, orig)
        else:
            sep = "\n\n" if orig and not orig.endswith("\n\n") else ("\n" if orig.endswith("\n") and not orig.endswith("\n\n") else "")
            if orig and not orig.endswith("\n"):
                sep = "\n\n"
            updated = orig + sep + block + "\n"
    else:
        updated = block + "\n"
    target_file.write_text(updated, encoding="utf-8")
    return True


def upsert_project_rule_file(
    project_dir: Path,
    repo_dir: Path,
    hub_dir: Path,
    confirm_mode: str,
    dry_run: bool,
) -> List[Path]:
    """Write or idempotently update project-local AGENTS.md (never touch $HOME)."""
    block = build_project_rule_block(project_dir, repo_dir, hub_dir, confirm_mode)
    agents_md = project_dir / "AGENTS.md"
    touched: List[Path] = []
    if _upsert_marked_block(agents_md, block, dry_run):
        touched.append(agents_md)

    claude_md = project_dir / "CLAUDE.md"
    if claude_md.exists():
        points_to_agents = False
        if claude_md.is_symlink():
            try:
                points_to_agents = claude_md.resolve(strict=False) == agents_md.resolve(strict=False)
            except OSError:
                points_to_agents = False
        if not points_to_agents and claude_md.is_file():
            if _upsert_marked_block(claude_md, block, dry_run):
                touched.append(claude_md)
    return touched


def is_allowed_repo_rel_path(rel_path: str) -> bool:
    norm = rel_path.replace("\\", "/")
    return any(p.match(norm) for p in ALLOWED_PATH_PATTERNS)


def is_report_doc_rel_path(rel_path: str) -> bool:
    norm = rel_path.replace("\\", "/")
    return any(p.match(norm) for p in REPORT_DOC_PATTERNS)


def collect_pending_rel_paths(repo_dir: Path) -> List[str]:
    """Return sorted relative paths of staged, modified tracked, or untracked non-ignored files."""
    if not (repo_dir / ".git").exists():
        return []
    pending: Set[str] = set()
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


def audit_paths(
    repo_dir: Path,
    rel_paths: List[str],
    max_bytes: int = MAX_SINGLE_FILE_BYTES,
) -> Tuple[List[str], List[str]]:
    """Run Tier-1 hard blocks (errors) + Tier-2 report quality advisories (warnings)."""
    errors: List[str] = []
    warnings: List[str] = []

    for rel in rel_paths:
        norm = rel.replace("\\", "/")
        if not is_allowed_repo_rel_path(norm):
            errors.append(f"非白名单仓库路径禁止上传：{norm}")
            continue
        if is_blocked_filename_or_ext(norm):
            errors.append(f"敏感文件、环境缓存或禁用大模型/压缩文件类型禁止上传：{norm}")
            continue

        p = repo_dir / rel
        if p.is_symlink():
            errors.append(f"禁止上传符号链接：{norm}")
            continue
        if not p.is_file():
            continue

        data = p.read_bytes()
        if len(data) > max_bytes:
            mb = len(data) / (1024 * 1024)
            limit_mb = max_bytes / (1024 * 1024)
            errors.append(f"文件超出 {limit_mb:.1f} MiB 上限 ({mb:.2f} MiB)：{norm}")
            continue

        if is_allowed_binary_asset(norm):
            continue

        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(f"禁止上传非白名单图表/PDF 的未知二进制文件：{norm}")
            continue
        if "\x00" in text:
            errors.append(f"检测到二进制/空字节内容：{norm}")
            continue

        is_report = is_report_doc_rel_path(norm)
        for idx, line in enumerate(text.splitlines(), start=1):
            if SECRET_PATTERN.search(line):
                errors.append(f"检测到疑似私钥/密钥/Token/带签名链接：{norm}:{idx}（已拦截）")

            if is_report:
                if LOCAL_ABS_PATH_PATTERN.search(line):
                    warnings.append(
                        f"死链提醒：{norm}:{idx} 含本机路径，导师端打不开；建议用 attach/sync-code 上传并改相对路径"
                    )
                for leak in AI_DRAFT_LEAK_PATTERNS:
                    if leak in line:
                        warnings.append(f"AI草稿残留提醒：{norm}:{idx} 含“{leak}”，建议清理后再给导师")
                for em in EMAIL_PATTERN.findall(line):
                    if not em.lower().endswith("@users.noreply.github.com"):
                        warnings.append(f"PII 确认提醒：{norm}:{idx} 含邮箱地址，请确认是否有意公开给导师")
                if CN_PHONE_PATTERN.search(line):
                    warnings.append(f"PII 确认提醒：{norm}:{idx} 含疑似 11 位手机号，请确认是否有意写入")

        if re.match(r"^weekly/\d{4}-W\d{2}/evidence\.md$", norm) and len(text) > 6000:
            warnings.append(f"篇幅提醒：{norm} 共 {len(text)} 字符（建议 ≤6000 字符 / 2 页）")
        if re.match(r"^weekly/\d{4}-W\d{2}/judgment\.md$", norm) and len(text) > 800:
            warnings.append(f"篇幅提醒：{norm} 共 {len(text)} 字符（判断页建议 ≤300–500 字）")

    return errors, warnings


def strip_markdown_chrome(text: str) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    lines: List[str] = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if re.match(r"^[0-9]+\.\s*\*\*.*\*\*：?$", s):
            continue
        if s.startswith("- **提交人**") or s.startswith("- **填写要求**") or s.startswith("- **要求**") or s.startswith("- **硬性约束**"):
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

    if has_ev and has_jd:
        return True, "submitted_human"
    if has_rep:
        return True, "submitted_ai_mvp"
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


def classify_preview_kind(rel_path: str) -> str:
    norm = rel_path.replace("\\", "/")
    ext = Path(norm).suffix.lower()
    if norm.startswith("code/"):
        if ext in ALLOWED_BINARY_ASSET_EXTS:
            return "[figure]"
        if ext in {".csv", ".tsv", ".json", ".yaml", ".yml"}:
            return "[table/cfg]"
        return "[code]"
    if "/artifacts/" in norm:
        if ext in ALLOWED_BINARY_ASSET_EXTS:
            return "[figure]"
        if ext in {".csv", ".tsv", ".json"}:
            return "[table]"
        return "[artifact]"
    if ext in {".md", ".yaml", ".yml"} or norm in {"README.md", ".gitignore"}:
        return "[doc]"
    return "[asset]"


def format_plaintext_preview(repo_dir: Path, rel_paths: List[str]) -> str:
    if not rel_paths:
        return "(当前无新增或修改的待同步文件)"

    summary_lines = ["清单总览："]
    for rel in rel_paths:
        p = repo_dir / rel
        kind = classify_preview_kind(rel)
        if not p.is_file():
            summary_lines.append(f"  - {kind} {rel} (deleted)")
        else:
            summary_lines.append(f"  - {kind} {rel} ({p.stat().st_size} bytes)")

    blocks: List[str] = ["\n".join(summary_lines)]
    for rel in rel_paths:
        norm = rel.replace("\\", "/")
        p = repo_dir / rel
        if not p.is_file():
            continue
        ext = Path(norm).suffix.lower()
        expand = (
            is_report_doc_rel_path(norm)
            or norm in {"README.md", "profile.md"}
            or re.match(r"^weekly/\d{4}-W\d{2}/meta\.yaml$", norm) is not None
            or norm.startswith("projects/")
        )
        if not expand or ext in ALLOWED_BINARY_ASSET_EXTS:
            continue
        size = p.stat().st_size
        text = p.read_text(encoding="utf-8", errors="replace").rstrip("\n")
        if len(text) > PREVIEW_TEXT_CHAR_LIMIT:
            head = text[:5500]
            tail = text[-3500:]
            text = f"{head}\n\n... [preview truncated {len(text) - 9000} chars] ...\n\n{tail}"
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
            'report_mode: "dual_track"\n'
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

    ev_p = wdir / "evidence.md"
    jd_p = wdir / "judgment.md"
    rep_p = wdir / "report.md"
    art_dir = wdir / "artifacts"
    has_artifacts = art_dir.is_dir() and any(art_dir.rglob("*"))

    if require_human:
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
        present_doc = [p for p in (ev_p, rep_p, jd_p) if p.is_file()]
        if not present_doc and not has_artifacts:
            errors.append(f"weekly/{week}/ 下缺少 evidence.md、report.md 或 artifacts/ 内容")
            return errors, warnings
        for p in present_doc:
            txt = p.read_text(encoding="utf-8")
            for ph in UNFILLED_PLACEHOLDERS:
                if ph in txt:
                    errors.append(f"{p.name} 仍含未替换占位符：{ph}")
        if ev_p.is_file() and not jd_p.is_file():
            warnings.append(
                f"周报双轨提醒：weekly/{week}/ 尚缺本人判断页 judgment.md（≤300 字）；"
                "本次可先推送证据页/附件，截稿前请补全判断页。"
            )
        elif has_artifacts and not ev_p.is_file() and not rep_p.is_file():
            warnings.append(f"提示：weekly/{week}/ 仅上传了 artifacts/，提交周报时别忘了生成 evidence.md 与 judgment.md。")

    if errors:
        return errors, warnings

    now_str = now_shanghai().isoformat(timespec="seconds")
    new_lines = []
    found_sub = False
    for line in meta_p.read_text(encoding="utf-8").splitlines():
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


def _copy_asset_file(
    src_file: Path,
    dest_file: Path,
    repo_dir: Path,
    allowed_dirs: Sequence[str],
    max_bytes: int,
) -> Tuple[bool, str]:
    """Validate src_file strictly within jail and copy into repo_dir."""
    if src_file.is_symlink():
        return False, f"跳过符号链接：{src_file}"
    if not src_file.is_file():
        return False, f"跳过非普通文件：{src_file}"

    ok_src, msg_src = assert_path_in_allowed_dirs(src_file, allowed_dirs)
    if not ok_src:
        return False, msg_src

    try:
        rel_target = str(dest_file.resolve(strict=False).relative_to(repo_dir.resolve(strict=False))).replace("\\", "/")
    except ValueError:
        return False, f"目标路径越出仓库范围：{dest_file}"

    if not is_allowed_repo_rel_path(rel_target):
        return False, f"目标路径不在仓库白名单内：{rel_target}"
    if is_blocked_filename_or_ext(rel_target):
        return False, f"敏感/缓存/大模型扩展名文件被拦截：{src_file.name}"

    data = src_file.read_bytes()
    if len(data) > max_bytes:
        return False, f"文件超过 {max_bytes // (1024 * 1024)} MiB 上限，已跳过：{src_file.name}"

    if not is_allowed_binary_asset(rel_target):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return False, f"未知二进制文件已跳过：{src_file.name}"
        if "\x00" in text:
            return False, f"含空字节二进制内容已跳过：{src_file.name}"
        for idx, line in enumerate(text.splitlines(), start=1):
            if SECRET_PATTERN.search(line):
                return False, f"检测到疑似私钥/密钥/Token（{src_file.name}:{idx}），已拒绝复制"

    dest_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_file, dest_file)
    return True, rel_target


def _resolve_input_source(raw_path: str, base_dir: Path) -> Path:
    expanded = Path(raw_path).expanduser()
    if expanded.is_absolute():
        return expanded
    return base_dir / expanded


def _git_check_ignored_set(source_root: Path, candidate_paths: List[Path]) -> Set[Path]:
    if not candidate_paths or not shutil.which("git"):
        return set()
    probe = run_cmd(["git", "rev-parse", "--is-inside-work-tree"], cwd=source_root, check=False)
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        return set()
    rel_strs: List[str] = []
    rel_to_abs: Dict[str, Path] = {}
    for p in candidate_paths:
        try:
            r = str(p.relative_to(source_root)).replace("\\", "/")
        except ValueError:
            continue
        rel_strs.append(r)
        rel_to_abs[r] = p
    if not rel_strs:
        return set()
    payload = ("\0".join(rel_strs) + "\0").encode("utf-8")
    proc = run_cmd(
        ["git", "check-ignore", "--stdin", "-z"],
        cwd=source_root,
        check=False,
        input_bytes=payload,
    )
    ignored: Set[Path] = set()
    if proc.stdout:
        for item in proc.stdout.split("\0"):
            if item and item in rel_to_abs:
                ignored.add(rel_to_abs[item])
    return ignored


def perform_attach(
    repo_dir: Path,
    allowed_dirs: Sequence[str],
    from_dir_path: Path,
    week: str,
    target: str,
    subdir: Optional[str],
    raw_files: List[str],
    max_bytes: int,
) -> Tuple[List[str], List[str], int]:
    """Attach files/dirs from within allowlist into weekly/<week>/artifacts/ or code/. Returns (copied_rels, skipped_msgs, exit_code)."""
    copied: List[str] = []
    skipped: List[str] = []
    repo_resolved = repo_dir.resolve(strict=False)

    if target == "artifacts":
        dest_root = repo_dir / "weekly" / week / "artifacts"
    else:
        dest_root = repo_dir / "code"
    if subdir:
        clean_sub = subdir.strip("/").replace("\\", "/")
        if ".." in Path(clean_sub).parts:
            return [], [f"无效 --subdir（含 ..）：{subdir}"], 5
        dest_root = dest_root / clean_sub

    for raw in raw_files:
        src = _resolve_input_source(raw, from_dir_path)
        if src.is_symlink():
            return copied, skipped + [f"[BLOCK] 拒绝符号链接越权来源：{src}"], 5
        ok_src, msg_src = assert_path_in_allowed_dirs(src, allowed_dirs)
        if not ok_src:
            return copied, skipped + [f"[BLOCK] {msg_src}"], 5
        if not src.exists():
            skipped.append(f"文件不存在，已跳过：{src}")
            continue

        if src.is_file():
            dst = dest_root / src.name
            ok_cp, info = _copy_asset_file(src, dst, repo_dir, allowed_dirs, max_bytes)
            if ok_cp:
                copied.append(info)
            else:
                skipped.append(info)
            continue

        if src.is_dir():
            base_name = src.name
            for root_str, dirs, files in os.walk(src, followlinks=False):
                root_p = Path(root_str)
                dirs[:] = sorted(
                    d for d in dirs if d not in SKIP_DIR_NAMES and not (root_p / d).is_symlink()
                )
                for fname in sorted(files):
                    f_p = root_p / fname
                    if f_p.resolve(strict=False) == repo_resolved or str(f_p.resolve(strict=False)).startswith(str(repo_resolved) + os.sep):
                        continue
                    rel_under = f_p.relative_to(src)
                    dst = dest_root / base_name / rel_under
                    ok_cp, info = _copy_asset_file(f_p, dst, repo_dir, allowed_dirs, max_bytes)
                    if ok_cp:
                        copied.append(info)
                    else:
                        skipped.append(info)
    return copied, skipped, 0


def perform_sync_code(
    repo_dir: Path,
    allowed_dirs: Sequence[str],
    from_dir_path: Path,
    source_arg: str,
    dest_arg: Optional[str],
    max_files: int,
    delete_extra: bool,
    max_bytes: int,
) -> Tuple[List[str], List[str], List[str], str, int]:
    """Sync source tree inside allowlist into repo_dir/code/<dest>. Returns (copied, skipped, deleted, dest_rel, rc)."""
    src_raw = _resolve_input_source(source_arg, from_dir_path)
    if src_raw.is_symlink():
        return [], [f"[BLOCK] 拒绝符号链接目录：{src_raw}"], [], "", 5
    ok_src, msg_src = assert_path_in_allowed_dirs(src_raw, allowed_dirs)
    if not ok_src:
        return [], [f"[BLOCK] {msg_src}"], [], "", 5
    src_root = src_raw.resolve()
    if not src_root.is_dir():
        return [], [f"来源目录不存在：{src_root}"], [], "", 2

    matched_root = find_matching_allowed_root(from_dir_path, allowed_dirs) or from_dir_path.resolve()
    if dest_arg:
        clean_dest = dest_arg.strip("/").replace("\\", "/")
        if not clean_dest.startswith("code/"):
            clean_dest = f"code/{clean_dest}"
        if ".." in Path(clean_dest).parts:
            return [], [f"无效 --dest 路径：{dest_arg}"], [], "", 5
    else:
        clean_dest = f"code/{matched_root.name}"

    dest_root = repo_dir / clean_dest
    repo_resolved = repo_dir.resolve(strict=False)

    candidates: List[Path] = []
    skipped: List[str] = []
    for root_str, dirs, files in os.walk(src_root, followlinks=False):
        root_p = Path(root_str)
        kept_dirs: List[str] = []
        for d in sorted(dirs):
            dp = root_p / d
            if d in SKIP_DIR_NAMES or dp.is_symlink():
                continue
            dp_res = dp.resolve(strict=False)
            if dp_res == repo_resolved or str(dp_res).startswith(str(repo_resolved) + os.sep):
                continue
            kept_dirs.append(d)
        dirs[:] = kept_dirs

        for fname in sorted(files):
            fp = root_p / fname
            if fp.is_symlink():
                skipped.append(f"跳过符号链接：{fp.name}")
                continue
            candidates.append(fp)

    ignored_set = _git_check_ignored_set(src_root, candidates)
    filtered: List[Path] = []
    for fp in candidates:
        if fp in ignored_set:
            continue
        filtered.append(fp)

    copied: List[str] = []
    desired_rels: Set[str] = set()
    for fp in filtered:
        rel_under = fp.relative_to(src_root)
        rel_str = str(rel_under).replace("\\", "/")
        if is_blocked_filename_or_ext(rel_str):
            skipped.append(f"跳过敏感/禁用文件：{rel_str}")
            continue
        if len(copied) >= max_files:
            skipped.append(f"已达到单次 --max-files {max_files} 上限，截断剩余文件（如 {rel_str}）")
            break
        dst = dest_root / rel_under
        ok_cp, info = _copy_asset_file(fp, dst, repo_dir, allowed_dirs, max_bytes)
        if ok_cp:
            copied.append(info)
            desired_rels.add(info)
        else:
            skipped.append(info)

    deleted: List[str] = []
    if delete_extra and dest_root.is_dir():
        for root_str, _, files in os.walk(dest_root, followlinks=False):
            root_p = Path(root_str)
            for fname in sorted(files):
                ep = root_p / fname
                rel_repo = str(ep.relative_to(repo_dir)).replace("\\", "/")
                if rel_repo not in desired_rels:
                    ep.unlink()
                    deleted.append(rel_repo)

    return copied, skipped, deleted, clean_dest, 0


def cmd_init(args: argparse.Namespace) -> int:
    hub_dir = Path(args.hub_dir).expanduser().resolve() if args.hub_dir else default_hub_dir()
    repo_dir = Path(args.repo_dir).expanduser().resolve()
    policy_file = Path(args.policy_file).expanduser().resolve()

    if not args.project_dir:
        print(
            "[error] 必须显式指定一个由本人确认的科研工作目录：--project-dir \"<科研目录>\"。\n"
            "本工具绝不触碰任何全局 Agent 规则文件（~/.claude / ~/.codex / ~/.gemini / ~/.cursor）。",
            file=sys.stderr,
        )
        return 2

    project_dir = Path(args.project_dir).expanduser().resolve()
    if not project_dir.is_dir():
        print(f"[error] 指定的科研目录不存在：{project_dir}。请确认真实路径后重试。", file=sys.stderr)
        return 2

    version_cfg, _ = load_hub_configs(hub_dir)
    repo_name = args.repo_name or version_cfg.get("default_repo_name", DEFAULT_REPO_NAME)
    default_collaborators = version_cfg.get("required_collaborators", DEFAULT_ADVISOR_COLLABORATORS)
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
                f"[note] 检测到当前 GitHub 账号 {owner} 为导师角色：导师端可使用 advisor_collect.py 汇总，通常无需安装学生端。"
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

    collab_label = "、".join(f"`{c['github']}`" for c in collaborators) or "仅本人"
    write_if_missing(repo_dir / ".gitignore", STUDENT_GITIGNORE, args.dry_run)
    write_if_missing(
        repo_dir / "README.md",
        (
            f"# {owner} · 个人私有科研仓库\n\n"
            f"- 可见性：Private（仅本人与授权协作者 {collab_label} 可见；汇总脚本仅只读拉取）\n"
            "- 允许目录：`progress/YYYY-MM-DD.md`、`weekly/YYYY-Www/{evidence.md,judgment.md,report.md,artifacts/}`、`code/`、`sessions/`、`projects/`\n"
        ),
        args.dry_run,
    )
    write_if_missing(
        repo_dir / "profile.md",
        (
            "# 成员档案（无遥测）\n\n"
            f"- **GitHub ID**：{owner}\n"
            f"- **仓库地址**：{full_repo_name}\n"
            "- **称呼（选填）**：待补充\n"
            "- **主课题（选填）**：待补充\n"
        ),
        args.dry_run,
    )
    for sub in ("progress", "weekly", "code", "sessions"):
        write_if_missing(repo_dir / sub / ".gitkeep", "", args.dry_run)

    install_pre_commit_hook(repo_dir, args.dry_run)

    mode = getattr(args, "mode", "manual") or "manual"
    confirm_mode = getattr(args, "confirm_mode", "confirm") or "confirm"

    touched_rule_files: List[Path] = []
    if mode == "project-rule":
        touched_rule_files = upsert_project_rule_file(
            project_dir=project_dir,
            repo_dir=repo_dir,
            hub_dir=hub_dir,
            confirm_mode=confirm_mode,
            dry_run=args.dry_run,
        )

    policy = load_policy(policy_file)
    allowed_dirs = list(policy.get("allowed_project_dirs", []))
    proj_str = str(project_dir)
    if proj_str not in allowed_dirs:
        allowed_dirs.append(proj_str)

    project_modes = dict(policy.get("project_modes", {}))
    project_modes[proj_str] = {"mode": mode, "confirm_mode": confirm_mode}

    policy.update(
        {
            "schema_version": version_cfg.get("schema_version", SCHEMA_VERSION),
            "repo_dir": str(repo_dir),
            "hub_dir": str(hub_dir),
            "github_login": owner,
            "repo": full_repo_name,
            "mode": mode,
            "confirm_mode": confirm_mode,
            "require_confirm": confirm_mode != "auto-in-allowlist",
            "allowed_project_dirs": allowed_dirs,
            "project_modes": project_modes,
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

    print(f"[done] v{version_cfg.get('schema_version', SCHEMA_VERSION)} 初始化完成：私有库={full_repo_name}")
    print(f"[ok] 本地白名单（chmod 0600）：{policy_file} -> {proj_str}")
    print(f"[ok] 同步模式：--mode {mode} | 确认策略：--confirm-mode {confirm_mode}")
    print(f"[ok] 已安装 Git pre-commit 护栏：{repo_dir / '.git' / 'hooks' / 'pre-commit'}")
    if mode == "project-rule":
        joined = ", ".join(str(p) for p in touched_rule_files) or str(project_dir / "AGENTS.md")
        print(f"[ok] 仅在授权工作目录写入快捷同步规则（绝未触碰全局规则）：{joined}")
    else:
        print("[info] 纯手动零侵入模式：绝未写入任何全局或项目级 AGENTS.md / CLAUDE.md。")
    return 0


def cmd_attach(args: argparse.Namespace) -> int:
    hub_dir = Path(args.hub_dir).expanduser().resolve() if args.hub_dir else default_hub_dir()
    repo_dir = Path(args.repo_dir).expanduser().resolve()
    policy_file = Path(args.policy_file).expanduser().resolve()

    if not repo_dir.is_dir() or not (repo_dir / ".git").exists():
        print(f"[error] 私有库尚未初始化：{repo_dir}", file=sys.stderr)
        return 1

    policy = load_policy(policy_file)
    ok_cwd, cwd_msg = assert_allowed_cwd(policy, args.from_dir)
    if not ok_cwd:
        print(f"[BLOCK] {cwd_msg}", file=sys.stderr)
        return 5

    version_cfg, schedule = load_hub_configs(hub_dir)
    max_bytes = int(version_cfg.get("limits", {}).get("max_single_file_bytes", MAX_SINGLE_FILE_BYTES))
    dt = now_shanghai(getattr(args, "now", None))
    week = args.week or evaluate_schedule(schedule, dt, repo_dir)["active_week"]
    allowed_dirs = policy.get("allowed_project_dirs", [])

    copied, skipped, rc = perform_attach(
        repo_dir=repo_dir,
        allowed_dirs=allowed_dirs,
        from_dir_path=Path(cwd_msg),
        week=week,
        target=args.target,
        subdir=args.subdir,
        raw_files=args.files,
        max_bytes=max_bytes,
    )
    for msg in skipped:
        print(f"[skip] {msg}", file=sys.stderr if rc != 0 else sys.stdout)
    if rc != 0:
        return rc

    if not copied:
        print("[warn] 没有成功复制任何文件。", file=sys.stderr)
        return 1

    print(f"[ok] 已附加 {len(copied)} 个可审计文件到私有库（可按下方相对链接写进 evidence.md）：")
    for rel in copied:
        if rel.startswith(f"weekly/{week}/"):
            ev_link = rel[len(f"weekly/{week}/"):]
        else:
            ev_link = f"../../{rel}"
        print(f"  - {rel}  ->  [{Path(rel).name}]({ev_link})")
    return 0


def cmd_sync_code(args: argparse.Namespace) -> int:
    hub_dir = Path(args.hub_dir).expanduser().resolve() if args.hub_dir else default_hub_dir()
    repo_dir = Path(args.repo_dir).expanduser().resolve()
    policy_file = Path(args.policy_file).expanduser().resolve()

    if not repo_dir.is_dir() or not (repo_dir / ".git").exists():
        print(f"[error] 私有库尚未初始化：{repo_dir}", file=sys.stderr)
        return 1

    policy = load_policy(policy_file)
    ok_cwd, cwd_msg = assert_allowed_cwd(policy, args.from_dir)
    if not ok_cwd:
        print(f"[BLOCK] {cwd_msg}", file=sys.stderr)
        return 5

    version_cfg, _ = load_hub_configs(hub_dir)
    max_bytes = int(version_cfg.get("limits", {}).get("max_single_file_bytes", MAX_SINGLE_FILE_BYTES))
    allowed_dirs = policy.get("allowed_project_dirs", [])

    copied, skipped, deleted, dest_rel, rc = perform_sync_code(
        repo_dir=repo_dir,
        allowed_dirs=allowed_dirs,
        from_dir_path=Path(cwd_msg),
        source_arg=args.source,
        dest_arg=args.dest,
        max_files=args.max_files,
        delete_extra=args.delete,
        max_bytes=max_bytes,
    )
    for msg in skipped[:25]:
        print(f"[skip] {msg}", file=sys.stderr if rc != 0 else sys.stdout)
    if len(skipped) > 25:
        print(f"[skip] ... 另有 {len(skipped) - 25} 项已跳过")
    if rc != 0:
        return rc

    print(
        f"[ok] 已同步代码目录至 {dest_rel}/：复制 {len(copied)} 个文件"
        + (f"，清理冗余 {len(deleted)} 个" if deleted else "")
        + f"（总跳过 {len(skipped)} 项）"
    )
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

    errs, warns = audit_paths(repo_dir, pending, max_bytes=max_bytes)
    for w in warns:
        print(f"[WARN] {w}")
    if errs:
        for e in errs:
            print(f"[BLOCK] {e}", file=sys.stderr)
        return 3

    print(f"[preview] 授权目录校验通过：{cwd_msg}")
    print(f"[preview] Tier-1 硬门禁全部通过，本次拟同步 {len(pending)} 个文件：\n")
    print(format_plaintext_preview(repo_dir, pending))
    if policy.get("confirm_mode") == "auto-in-allowlist":
        print("\n[preview] 当前策略为白名单内无感推送（judgment.md 仍需 --confirm）。")
    else:
        print("\n[preview] 以上内容尚未提交或推送。确认无误后，请运行带 --confirm 的 push / upload 命令。")
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
    dt = now_shanghai(getattr(args, "now", None))
    sched_res = evaluate_schedule(schedule, dt, repo_dir)
    week = args.week or sched_res["active_week"]

    pending = collect_pending_rel_paths(repo_dir)
    errs, warns = audit_paths(repo_dir, pending, max_bytes=max_bytes)
    for w in warns:
        print(f"[WARN] {w}")
    if errs:
        for e in errs:
            print(f"[BLOCK] {e}", file=sys.stderr)
        return 3

    mode = args.mode
    if mode == "auto":
        if any(p.startswith(f"weekly/{week}/") for p in pending):
            mode = "weekly"
        elif pending and all(p.startswith("code/") for p in pending):
            mode = "code"
        else:
            mode = "progress"

    if mode == "weekly":
        github_login = policy.get("github_login") or detect_github_login(skip_remote=True)
        val_errs, val_warns = ensure_weekly_meta_and_validate(
            repo_dir, week, sched_res["require_human_judgment"], limits, github_login
        )
        for w in val_warns:
            print(f"[WARN] {w}")
        if val_errs:
            for e in val_errs:
                print(f"[BLOCK] {e}", file=sys.stderr)
            return 4
        pending = collect_pending_rel_paths(repo_dir)
        errs2, warns2 = audit_paths(repo_dir, pending, max_bytes=max_bytes)
        for w in warns2:
            if w not in warns:
                print(f"[WARN] {w}")
        if errs2:
            for e in errs2:
                print(f"[BLOCK] {e}", file=sys.stderr)
            return 3

    touches_judgment = any(
        re.match(r"^weekly/\d{4}-W\d{2}/judgment\.md$", p.replace("\\", "/")) for p in pending
    )
    confirm_mode = policy.get("confirm_mode", "confirm")
    must_confirm = (confirm_mode != "auto-in-allowlist") or touches_judgment

    if must_confirm and not args.confirm:
        reason = "检测到修改了本人判断页 (judgment.md)，必须经本人亲自确认" if touches_judgment else "当前策略要求推送前确认"
        print(
            f"[BLOCK] 安全门禁（{reason}）：未传 --confirm，已阻止推送。请先向本人展示下方待推摘要，获同意后加 --confirm 执行：\n",
            file=sys.stderr,
        )
        print(format_plaintext_preview(repo_dir, pending), file=sys.stderr)
        return 6

    if not must_confirm and not args.confirm and pending:
        print("[auto-in-allowlist] 白名单目录无感直推（未含 judgment.md），直接核验提交。")

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
        default_msg = (
            f"weekly({week}): sync report & artifacts"
            if mode == "weekly"
            else (f"code({dt.date().isoformat()}): sync code" if mode == "code" else f"progress({dt.date().isoformat()}): sync progress")
        )
        msg = args.message or default_msg
        commit_p = run_cmd(["git", "commit", "-m", msg], cwd=repo_dir, check=False)
        if commit_p.returncode != 0:
            err_txt = commit_p.stderr.strip() or commit_p.stdout.strip()
            print(f"[BLOCK] 提交被 Git hook 拦截：{err_txt}", file=sys.stderr)
            return 3
        print(f"[ok] 已提交：{msg}（白名单目录：{cwd_msg}）")
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
                    print(f"[queued-offline] 远端推送暂未成功（{push_p.stderr.strip()[:120]}），已保留本地提交。")
            except Exception as exc:
                print(f"[queued-offline] 网络超时或暂不可用（{exc}），已保留本地提交。")
    return 0


def cmd_upload(args: argparse.Namespace) -> int:
    """One-shot seamless workflow: optional attach -> optional sync-code -> push."""
    hub_dir = Path(args.hub_dir).expanduser().resolve() if args.hub_dir else default_hub_dir()
    repo_dir = Path(args.repo_dir).expanduser().resolve()
    policy_file = Path(args.policy_file).expanduser().resolve()

    if not repo_dir.is_dir() or not (repo_dir / ".git").exists():
        print(f"[error] 私有库尚未初始化：{repo_dir}", file=sys.stderr)
        return 1

    policy = load_policy(policy_file)
    ok_cwd, cwd_msg = assert_allowed_cwd(policy, args.from_dir)
    if not ok_cwd:
        print(f"[BLOCK] {cwd_msg}", file=sys.stderr)
        return 5

    version_cfg, schedule = load_hub_configs(hub_dir)
    max_bytes = int(version_cfg.get("limits", {}).get("max_single_file_bytes", MAX_SINGLE_FILE_BYTES))
    dt = now_shanghai(getattr(args, "now", None))
    week = args.week or evaluate_schedule(schedule, dt, repo_dir)["active_week"]
    allowed_dirs = policy.get("allowed_project_dirs", [])

    if args.attach:
        copied, skipped, rc = perform_attach(
            repo_dir=repo_dir,
            allowed_dirs=allowed_dirs,
            from_dir_path=Path(cwd_msg),
            week=week,
            target="artifacts",
            subdir=None,
            raw_files=args.attach,
            max_bytes=max_bytes,
        )
        for msg in skipped:
            print(f"[skip] {msg}", file=sys.stderr if rc != 0 else sys.stdout)
        if rc != 0:
            return rc
        print(f"[ok] upload-attach：已复制 {len(copied)} 个附件到 weekly/{week}/artifacts/")

    if args.sync_code is not None:
        copied_c, skipped_c, _, dest_rel, rc_c = perform_sync_code(
            repo_dir=repo_dir,
            allowed_dirs=allowed_dirs,
            from_dir_path=Path(cwd_msg),
            source_arg=args.sync_code,
            dest_arg=None,
            max_files=200,
            delete_extra=False,
            max_bytes=max_bytes,
        )
        for msg in skipped_c[:15]:
            print(f"[skip] {msg}", file=sys.stderr if rc_c != 0 else sys.stdout)
        if rc_c != 0:
            return rc_c
        print(f"[ok] upload-code：已同步 {len(copied_c)} 个文件到 {dest_rel}/")

    return cmd_push(args)


def cmd_policy(args: argparse.Namespace) -> int:
    policy_file = Path(args.policy_file).expanduser().resolve()
    policy = load_policy(policy_file)
    if not policy:
        print(f"[info] 策略文件尚不存在：{policy_file}")
        return 1

    changed = False
    if getattr(args, "set_confirm_mode", None):
        cm = args.set_confirm_mode
        policy["confirm_mode"] = cm
        policy["require_confirm"] = cm != "auto-in-allowlist"
        policy["updated_at"] = now_shanghai().isoformat(timespec="seconds")
        changed = True
        print(f"[ok] 已更新 confirm_mode = {cm}")

    if getattr(args, "remove_project_dir", None):
        target = str(Path(args.remove_project_dir).expanduser().resolve(strict=False))
        dirs = list(policy.get("allowed_project_dirs", []))
        new_dirs = [d for d in dirs if str(Path(d).expanduser().resolve(strict=False)) != target]
        if len(new_dirs) == len(dirs):
            print(f"[warn] 未在白名单中找到目录：{target}")
        else:
            policy["allowed_project_dirs"] = new_dirs
            pm = dict(policy.get("project_modes", {}))
            pm.pop(target, None)
            policy["project_modes"] = pm
            policy["updated_at"] = now_shanghai().isoformat(timespec="seconds")
            changed = True
            print(f"[ok] 已从白名单移除目录：{target}")

    if changed:
        save_policy(policy_file, policy)

    if getattr(args, "show", False) or not changed:
        print(json.dumps(policy, ensure_ascii=False, indent=2))
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
        "schema_version": version_cfg.get("schema_version", SCHEMA_VERSION),
        "hub_dir": str(hub_dir),
        "repo_dir": str(repo_dir),
        "policy_file": str(policy_file),
        "mode": policy.get("mode", "manual"),
        "confirm_mode": policy.get("confirm_mode", "confirm"),
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
        print(
            f"[status] 协议版本: v{result['schema_version']} | 模式: {result['mode']}/{result['confirm_mode']} "
            f"| 授权目录数: {len(result['allowed_project_dirs'])} | 当前目录授权: {ok_cwd}"
        )
        print(f"[status] 本周({result['active_week']}) 状态: {result['submission_state']} | 窗口: {result['window_reason']} | 提醒: {result['action']}")
        print(f"[status] 待同步文件: {len(pending)} | 本地待推提交: {ahead}")
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
    print(f"[ok] 已手动升级协议中心并刷新护栏：{hub_dir}")
    return 0


def _add_common_repo_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--repo-dir", default="~/.yao-research/repo", help="本地私有库路径")
    p.add_argument("--hub-dir", default=None, help="本地协议中心路径")
    p.add_argument("--policy-file", default=str(default_policy_file()), help="本地白名单策略文件")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Yao Research Group 科研进展与代码图表同步工具 (v3.0)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="登记授权科研目录、建私有库(默认仅导师 taoyao2005)并装护栏")
    p_init.add_argument("--project-dir", default=None, help="必填：用户明确指定的一个科研工作目录")
    p_init.add_argument("--repo-name", default=None, help="私有仓库名（默认 yao-research-memory）")
    _add_common_repo_args(p_init)
    p_init.add_argument(
        "--collaborators",
        default=None,
        help="逗号分隔指定 GitHub 协作者，默认仅 taoyao2005；同组互看可传如 taoyao2005,teammate",
    )
    p_init.add_argument(
        "--mode",
        default="manual",
        choices=["manual", "project-rule"],
        help="manual=零侵入纯手动（默认）；project-rule=仅在授权工作目录写 AGENTS.md 无感规则（绝不碰全局）",
    )
    p_init.add_argument(
        "--confirm-mode",
        default="confirm",
        choices=["confirm", "auto-in-allowlist"],
        help="confirm=推送前需确认（默认）；auto-in-allowlist=白名单内进展/代码无感直推（judgment.md 仍需 --confirm）",
    )
    p_init.add_argument("--skip-remote", action="store_true", help="跳过远端 GitHub 建库")
    p_init.add_argument("--dry-run", action="store_true", help="仅预览操作不写文件")

    p_att = sub.add_parser("attach", help="把白名单目录内的实验图表/结果表/代码脚本附加到周报或 code/")
    _add_common_repo_args(p_att)
    p_att.add_argument("--from-dir", default=None, help="来源工作目录（默认当前 CWD）")
    p_att.add_argument("--week", default=None, help="目标周次（默认当前活跃周）")
    p_att.add_argument("--target", default="artifacts", choices=["artifacts", "code"], help="放进 weekly/<W>/artifacts/ 或 code/")
    p_att.add_argument("--subdir", default=None, help="可选子目录名")
    p_att.add_argument("--now", default=None, help=argparse.SUPPRESS)
    p_att.add_argument("files", nargs="+", help="要附加的文件或目录路径（必须位于白名单科研目录内）")

    p_sc = sub.add_parser("sync-code", help="把白名单科研目录的代码树安全同步到私有库 code/ 下供导师审计")
    _add_common_repo_args(p_sc)
    p_sc.add_argument("--from-dir", default=None, help="来源工作目录（默认当前 CWD）")
    p_sc.add_argument("--source", default=".", help="相对 --from-dir 或绝对源码目录路径（须在白名单内）")
    p_sc.add_argument("--dest", default=None, help="目标相对目录名，如 code/pfn-mdp（默认 code/<项目目录名>）")
    p_sc.add_argument("--max-files", type=int, default=200, help="单次最多同步文件数（默认 200）")
    p_sc.add_argument("--delete", action="store_true", help="清理目标目录中源端已删除的文件")

    p_prev = sub.add_parser("preview", help="校验白名单、执行分级审计并展示待上传清单与汇报明文")
    _add_common_repo_args(p_prev)
    p_prev.add_argument("--from-dir", default=None, help="来源工作目录（默认当前 CWD）")

    p_push = sub.add_parser("push", help="过护栏审计并提交推送（根据 confirm-mode 与是否含 judgment.md 决定确认要求）")
    _add_common_repo_args(p_push)
    p_push.add_argument("--confirm", action="store_true", help="确认推送标志（confirm 模式或修改 judgment.md 时必填）")
    p_push.add_argument("--from-dir", default=None, help="来源工作目录（默认当前 CWD）")
    p_push.add_argument("--mode", default="auto", choices=["auto", "progress", "weekly", "code"])
    p_push.add_argument("--week", default=None)
    p_push.add_argument("--message", default=None)
    p_push.add_argument("--skip-remote", action="store_true")
    p_push.add_argument("--now", default=None, help=argparse.SUPPRESS)

    p_up_one = sub.add_parser("upload", help="一站式上传：可选 --attach 与 --sync-code 后直接走 push 审计提交")
    _add_common_repo_args(p_up_one)
    p_up_one.add_argument("--from-dir", default=None, help="来源工作目录（默认当前 CWD）")
    p_up_one.add_argument("--week", default=None)
    p_up_one.add_argument("--attach", action="append", default=[], help="附加文件/目录到 weekly/<W>/artifacts/（可多次传）")
    p_up_one.add_argument("--sync-code", default=None, help="同时同步代码子目录或 . 到 code/")
    p_up_one.add_argument("--confirm", action="store_true")
    p_up_one.add_argument("--mode", default="auto", choices=["auto", "progress", "weekly", "code"])
    p_up_one.add_argument("--message", default=None)
    p_up_one.add_argument("--skip-remote", action="store_true")
    p_up_one.add_argument("--now", default=None, help=argparse.SUPPRESS)

    p_pol = sub.add_parser("policy", help="查看或调整本地白名单策略（confirm-mode / 移除目录）")
    p_pol.add_argument("--policy-file", default=str(default_policy_file()))
    p_pol.add_argument("--show", action="store_true", help="查看当前策略 JSON")
    p_pol.add_argument("--set-confirm-mode", default=None, choices=["confirm", "auto-in-allowlist"])
    p_pol.add_argument("--remove-project-dir", default=None, help="从白名单移除某个目录")

    for cmd_name in ("status", "preflight"):
        p_st = sub.add_parser(cmd_name, help="只读查看授权目录、同步策略、周报窗口与待推状态")
        _add_common_repo_args(p_st)
        p_st.add_argument("--from-dir", default=None)
        p_st.add_argument("--json", dest="json_out", action="store_true")
        p_st.add_argument("--now", default=None, help=argparse.SUPPRESS)

    p_cu = sub.add_parser("check-update", help="只读检查协议中心远端更新")
    p_cu.add_argument("--hub-dir", default=None)

    p_up = sub.add_parser("upgrade", help="手动升级本地协议中心并刷新 Git pre-commit 护栏")
    p_up.add_argument("--hub-dir", default=None)
    p_up.add_argument("--repo-dir", default="~/.yao-research/repo")

    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if args.command == "init":
        return cmd_init(args)
    if args.command == "attach":
        return cmd_attach(args)
    if args.command == "sync-code":
        return cmd_sync_code(args)
    if args.command == "preview":
        return cmd_preview(args)
    if args.command == "push":
        return cmd_push(args)
    if args.command == "upload":
        return cmd_upload(args)
    if args.command == "policy":
        return cmd_policy(args)
    if args.command in ("status", "preflight"):
        return cmd_status(args)
    if args.command == "check-update":
        return cmd_check_update(args)
    if args.command == "upgrade":
        return cmd_upgrade(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
