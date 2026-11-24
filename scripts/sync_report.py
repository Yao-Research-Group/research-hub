#!/usr/bin/env python3
"""Pull dynamic protocol specs, run privacy/secret/content gates, and sync student repo.

Zero third-party dependencies (Python 3.9+ stdlib only).
All file I/O explicitly specifies UTF-8 encoding.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple
import urllib.request

DEFAULT_HUB_REPO = "Yao-Research-Group/research-hub"
DEFAULT_HUB_BRANCH = "main"
MAX_SINGLE_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB

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
    "<请附仓库、分支与 commit hash>",
    "<本周精读论文与对照要点>",
    "<必填：本周未成功、负提升或被证伪的做法>",
    "<用一句话说清核心发现或真实卡点>",
    "<推翻或修正了什么旧假设>",
]


def current_iso_week() -> str:
    iso = date.today().isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def run_cmd(cmd: List[str], cwd: Optional[Path] = None, check: bool = True) -> subprocess.CompletedProcess:
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


def parse_simple_yaml(text: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        key = k.strip()
        val = v.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        result[key] = val
    return result


def flatten_file_paths(obj: Any) -> List[str]:
    paths: List[str] = []
    if isinstance(obj, str):
        paths.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            paths.extend(flatten_file_paths(v))
    elif isinstance(obj, list):
        for item in obj:
            paths.extend(flatten_file_paths(item))
    return paths


def fetch_hub_text(rel_path: str, hub_dir: Optional[Path], hub_repo: str, hub_branch: str) -> str:
    if hub_dir is not None:
        return (hub_dir / rel_path).read_text(encoding="utf-8")
    url = f"https://raw.githubusercontent.com/{hub_repo}/{hub_branch}/{rel_path}"
    req = urllib.request.Request(url, headers={"User-Agent": "yao-research-hub/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8")


def sync_protocol_cache(
    cache_dir: Path,
    hub_dir: Optional[Path],
    hub_repo: str,
    hub_branch: str,
    skip_remote: bool,
) -> Dict[str, Any]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached_version = cache_dir / "protocol" / "version.json"

    if skip_remote and hub_dir is None:
        if cached_version.exists():
            return json.loads(cached_version.read_text(encoding="utf-8"))
        return {"schema_version": "offline", "current_week": current_iso_week(), "limits": {}}

    try:
        raw_version = fetch_hub_text("protocol/version.json", hub_dir, hub_repo, hub_branch)
        manifest = json.loads(raw_version)
        cached_version.parent.mkdir(parents=True, exist_ok=True)
        cached_version.write_text(raw_version, encoding="utf-8")

        for rel in flatten_file_paths(manifest.get("files", {})):
            if not rel.endswith((".md", ".json", ".yaml", ".yml")):
                continue
            content = fetch_hub_text(rel, hub_dir, hub_repo, hub_branch)
            dest = cache_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
        print(
            f"[protocol] 已同步最新协议 v{manifest.get('schema_version', '?')} "
            f"(当前周次: {manifest.get('current_week', '?')}, 截止: {manifest.get('current_deadline', '?')})"
        )
        return manifest
    except Exception as exc:
        if cached_version.exists():
            print(f"[warn] 拉取远端协议失败（{exc}），回退使用本地缓存。")
            return json.loads(cached_version.read_text(encoding="utf-8"))
        print(f"[warn] 拉取远端协议失败且无本地缓存（{exc}），使用默认内置边界。")
        return {"schema_version": "fallback", "current_week": current_iso_week(), "limits": {}}


def parse_github_slug(remote_url: str) -> Optional[str]:
    m = re.search(r"github\.com[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?$", remote_url.strip())
    return m.group(1) if m else None


def verify_remote_private(repo_dir: Path) -> None:
    rem = run_cmd(["git", "remote", "get-url", "origin"], cwd=repo_dir, check=False)
    if rem.returncode != 0 or not rem.stdout.strip():
        print("[warn] 尚未配置 origin 远端，跳过远端私有性 API 校验。")
        return
    slug = parse_github_slug(rem.stdout.strip())
    if not slug:
        return
    if not shutil.which("gh"):
        raise RuntimeError("安全拦截：未检测到 gh CLI，无法验证远端仓库是否为 Private；离线校验请显式加 --skip-remote。")
    probe = run_cmd(["gh", "api", f"repos/{slug}"], check=False)
    if probe.returncode != 0:
        raise RuntimeError(f"安全拦截：无法验证 {slug} 可见性（{probe.stderr.strip()}），拒绝上传。")
    meta = json.loads(probe.stdout)
    if not meta.get("private", False):
        raise RuntimeError(
            f"安全拦截：远端仓库 {slug} 当前为 PUBLIC！"
            "已阻止同步，请立即在 GitHub Settings 将仓库改为 Private。"
        )
    print(f"[ok] 远端仓库私有性核验通过：{slug} (private=true)")


def iter_candidate_files(repo_dir: Path) -> List[Path]:
    """Return tracked and non-ignored untracked regular files inside repo_dir."""
    if (repo_dir / ".git").exists():
        proc = run_cmd(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=repo_dir,
            check=False,
        )
        if proc.returncode == 0:
            files: List[Path] = []
            for rel in proc.stdout.splitlines():
                p = repo_dir / rel.strip()
                if p.is_file() and not p.is_symlink():
                    files.append(p)
            return files

    files = []
    for root, dirs, names in os.walk(repo_dir):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".venv"}]
        for name in names:
            p = Path(root) / name
            if p.is_file() and not p.is_symlink():
                files.append(p)
    return files


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
            errors.append(f"文件超出大小限制 ({size} > {max_bytes} bytes)：{rel}")
            continue
        if ext in {".md", ".yaml", ".yml", ".json", ".txt", ".py", ".sh", ".csv", ".tsv"}:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                errors.append(f"文本文件非 UTF-8 编码：{rel}")
                continue
            for idx, line in enumerate(text.splitlines(), start=1):
                if SECRET_PATTERN.search(line):
                    errors.append(f"检测到疑似凭据/密钥/Token URL：{rel}:{idx}（已隐藏内容）")
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
        if s.startswith("- **提交人**") or s.startswith("- **填写要求**"):
            continue
        lines.append(s)
    return "\n".join(lines).strip()


def validate_weekly_submission(repo_dir: Path, week: str, limits: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    week_dir = repo_dir / "weekly" / week
    if not week_dir.is_dir():
        return [f"缺少本周目录：{week_dir}"], warnings

    meta_path = week_dir / "meta.yaml"
    ev_path = week_dir / "evidence.md"
    jd_path = week_dir / "judgment.md"

    for p in (meta_path, ev_path, jd_path):
        if not p.is_file():
            errors.append(f"缺少必填周报文件：{p.relative_to(repo_dir)}")
    if errors:
        return errors, warnings

    meta = parse_simple_yaml(meta_path.read_text(encoding="utf-8"))
    if meta.get("week") != week:
        errors.append(f"meta.yaml 中 week='{meta.get('week')}' 与目标周次 '{week}' 不一致")
    if not meta.get("student_name") or meta.get("student_name") in {"姓名", "<姓名>"}:
        errors.append("meta.yaml 未填写有效 student_name")
    req = meta.get("meeting_request", "")
    if req not in VALID_MEETING_REQUESTS:
        errors.append(f"meta.yaml 中 meeting_request='{req}' 无效，必须为 {sorted(VALID_MEETING_REQUESTS)} 之一")

    ev_text = ev_path.read_text(encoding="utf-8")
    jd_text = jd_path.read_text(encoding="utf-8")

    for placeholder in UNFILLED_PLACEHOLDERS:
        if placeholder in ev_text:
            errors.append(f"evidence.md 仍含未替换模板占位符：{placeholder}")
        if placeholder in jd_text:
            errors.append(f"judgment.md 仍含未替换模板占位符：{placeholder}")

    for required_sec in ("实验", "代码", "失败"):
        if required_sec not in ev_text:
            errors.append(f"evidence.md 缺少必填内容标识（需包含‘{required_sec}’章节，特别是失败尝试）")

    ev_body = strip_markdown_chrome(ev_text)
    if len(ev_body) < 80:
        errors.append(f"evidence.md 实质正文过短（{len(ev_body)} 字），缺少真实材料陈述与来源")
    max_ev = int(limits.get("max_evidence_chars", 6000))
    if len(ev_text) > max_ev:
        warnings.append(f"evidence.md 总长 {len(ev_text)} 字符（建议 ≤ {max_ev} 字符以保持 ≤2 页）")

    jd_body = strip_markdown_chrome(jd_text)
    min_jd = int(limits.get("min_judgment_chars", 40))
    max_jd = int(limits.get("max_judgment_chars", 800))
    if len(jd_body) < min_jd:
        errors.append(f"judgment.md 本人实质判断正文过短（{len(jd_body)} < {min_jd} 字）；缺判断页视为未提交")
    elif len(jd_body) > max_jd:
        warnings.append(f"judgment.md 正文偏长（{len(jd_body)} > {max_jd} 字符），建议压在半页约 300 字内")

    max_card_lines = int(limits.get("max_project_card_lines", 80))
    proj_dir = repo_dir / "projects"
    if proj_dir.is_dir():
        for card in sorted(proj_dir.glob("*.md")):
            line_count = len(card.read_text(encoding="utf-8").splitlines())
            if line_count > max_card_lines:
                warnings.append(f"项目活页卡 {card.name} 共 {line_count} 行，超出 {max_card_lines} 行覆写上限")

    return errors, warnings


def check_pending_reviews(repo_dir: Path, week: str) -> None:
    rev_dir = repo_dir / "reviews"
    if not rev_dir.is_dir():
        return
    advisor_file = rev_dir / f"{week}-advisor.md"
    ai_file = rev_dir / f"{week}-ai-review.md"
    reply_file = rev_dir / f"{week}-reply.md"
    found = [f.name for f in (advisor_file, ai_file) if f.is_file()]
    if found and not reply_file.is_file():
        print(f"[reminder] reviews/ 下有新反馈 ({', '.join(found)})，尚未见 {reply_file.name} 书面回复。")


def stamp_submitted_at_if_empty(meta_path: Path) -> None:
    lines = meta_path.read_text(encoding="utf-8").splitlines()
    now_str = datetime.now().astimezone().isoformat(timespec="seconds")
    updated: List[str] = []
    replaced = False
    for line in lines:
        if line.strip().startswith("submitted_at:"):
            val = line.split(":", 1)[1].strip().strip('"\'')
            if not val:
                updated.append(f'submitted_at: "{now_str}"')
                replaced = True
                continue
        updated.append(line)
    if not replaced and not any(l.strip().startswith("submitted_at:") for l in lines):
        updated.append(f'submitted_at: "{now_str}"')
        replaced = True
    if replaced:
        meta_path.write_text("\n".join(updated) + "\n", encoding="utf-8")


def git_commit_and_push(repo_dir: Path, mode: str, week: str, message: Optional[str], skip_remote: bool) -> None:
    if not (repo_dir / ".git").exists():
        raise RuntimeError(f"{repo_dir} 不是 Git 仓库，请先运行 init_student.py。")

    branch_proc = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir, check=False)
    branch = branch_proc.stdout.strip() or "main"
    if branch == "HEAD":
        branch = "main"

    if not skip_remote:
        rem_check = run_cmd(["git", "remote", "get-url", "origin"], cwd=repo_dir, check=False)
        if rem_check.returncode == 0 and rem_check.stdout.strip():
            ls_rem = run_cmd(["git", "ls-remote", "--heads", "origin", branch], cwd=repo_dir, check=False)
            if ls_rem.returncode == 0 and ls_rem.stdout.strip():
                run_cmd(["git", "pull", "--rebase", "--autostash", "origin", branch], cwd=repo_dir)

    run_cmd(["git", "add", "-A"], cwd=repo_dir)
    status = run_cmd(["git", "status", "--porcelain"], cwd=repo_dir).stdout.strip()
    if status:
        commit_msg = message or (f"weekly({week}): update report" if mode == "weekly" else f"daily({date.today().isoformat()}): sync research log")
        run_cmd(["git", "commit", "-m", commit_msg], cwd=repo_dir)
        print(f"[ok] 已提交本地变更：{commit_msg}")
    else:
        print("[ok] 工作区无新变更（already up to date）。")

    if not skip_remote:
        rem_check = run_cmd(["git", "remote", "get-url", "origin"], cwd=repo_dir, check=False)
        if rem_check.returncode == 0 and rem_check.stdout.strip():
            run_cmd(["git", "push", "-u", "origin", branch], cwd=repo_dir)
            print(f"[ok] 已推送至远端 origin/{branch}")
        else:
            print("[warn] 未配置 origin，已完成本地提交但未推送远端。")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="校验并同步学生私有科研记忆与周报仓库")
    parser.add_argument("--repo-dir", default="~/.yao-research/repo", help="本地私有记忆仓库路径")
    parser.add_argument(
        "--mode",
        default="check",
        choices=["pull-protocol", "daily", "weekly", "check"],
        help="执行模式",
    )
    parser.add_argument("--week", default=None, help="目标周次（如 2026-W48）")
    parser.add_argument("--push", action="store_true", help="通过全部门禁后自动 commit 并 push")
    parser.add_argument("--skip-remote", action="store_true", help="跳过远端网络/API 操作（离线测试用）")
    parser.add_argument("--hub-dir", default=None, help="本地协议中心路径（离线/测试用）")
    parser.add_argument("--hub-repo", default=None, help="覆盖协议中心仓库")
    parser.add_argument("--hub-branch", default=None, help="覆盖协议中心分支")
    parser.add_argument("--cache-dir", default="~/.yao-research/protocol-cache", help="协议缓存目录")
    parser.add_argument("--message", default=None, help="自定义 Git 提交说明")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    repo_dir = Path(args.repo_dir).expanduser().resolve()
    hub_dir = Path(args.hub_dir).expanduser().resolve() if args.hub_dir else None
    cache_dir = Path(args.cache_dir).expanduser().resolve()

    hub_repo = args.hub_repo or DEFAULT_HUB_REPO
    hub_branch = args.hub_branch or DEFAULT_HUB_BRANCH
    hub_cfg = repo_dir / ".yao-hub.json"
    if hub_cfg.is_file() and args.hub_repo is None:
        try:
            loaded = json.loads(hub_cfg.read_text(encoding="utf-8"))
            hub_repo = loaded.get("hub_repo", hub_repo)
            hub_branch = loaded.get("hub_branch", hub_branch)
        except Exception:
            pass

    manifest = sync_protocol_cache(cache_dir, hub_dir, hub_repo, hub_branch, args.skip_remote)
    limits = manifest.get("limits", {})
    week = args.week or manifest.get("current_week") or current_iso_week()

    if repo_dir.is_dir():
        check_pending_reviews(repo_dir, week)

    if args.mode == "pull-protocol":
        return 0

    if not repo_dir.is_dir():
        print(f"[error] 私有仓库目录不存在：{repo_dir}", file=sys.stderr)
        return 1

    if not args.skip_remote:
        verify_remote_private(repo_dir)

    max_bytes = int(limits.get("max_single_file_bytes", MAX_SINGLE_FILE_BYTES))
    sec_errors = scan_secrets_and_sizes(repo_dir, max_bytes)
    if sec_errors:
        for err in sec_errors:
            print(f"[BLOCK] {err}", file=sys.stderr)
        return 3

    if args.mode in ("weekly", "check"):
        val_errors, val_warnings = validate_weekly_submission(repo_dir, week, limits)
        for w in val_warnings:
            print(f"[warn] {w}")
        if val_errors:
            for err in val_errors:
                print(f"[BLOCK] {err}", file=sys.stderr)
            return 4
        print(f"[ok] 周报 {week} 结构、来源标记与人机分离校验通过。")

    if args.push:
        if args.mode == "weekly":
            stamp_submitted_at_if_empty(repo_dir / "weekly" / week / "meta.yaml")
        git_commit_and_push(repo_dir, args.mode, week, args.message, args.skip_remote)
    else:
        print("[ok] 干跑校验全部通过（加 --push 执行提交与推送）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
