#!/usr/bin/env python3
"""Advisor collector & AI pre-read packager (v3.0).

Accepts collaborator invites for yao-research-* private repos, pulls read-only clones
locally so the advisor's AI can directly inspect code, figures, artifacts, progress logs,
evidence pages, and human judgment pages, and generates digest + pre-read + HTML outputs.

Zero third-party dependencies (Python 3.9+ stdlib only). All I/O uses UTF-8.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
import html
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

BUILTIN_ADVISOR_PROMPT = """阅读这名学生的证据页（evidence.md 或 report.md）与判断页（judgment.md），并按需直接抽查其本地仓库中的代码（code/）与实验图表（weekly/<W>/artifacts/），输出：
1. 3 行摘要：本周实际做了什么、证据是否充分、下一步是否清楚；
2. 结论与证据对照：逐条核查判断页中的结论在证据页/代码/实验产物中是否有支持，标注【有支持 / 部分支持 / 无支持】，并指明位置与来源；
3. 缺口与未回答问题：学生没有回答但应该回答的关键问题（如基线口径、停止条件、失败尝试）；
4. 最值得讨论的问题（最多 3 个，按重要性排序）：写成具体问题，不下结论；
5. 建议路由：【不需要开会 / 小组讨论 / 1:1 / 周四深度评审或决策 / 周六产品评审】五选一并说明理由。
要求：意见写成问题，不确定处标明 [不确定]，不写表扬和客套话。
读完全部学生后，另外输出：① 跨课题重复、冲突与共同阻塞；② 按紧急程度排序的本周需要老师关注的名单（每人一句理由）；③ 周二路由与周四深度评审建议议程。"""

UNFILLED_TOKENS = (
    "<请用本人语言填写>",
    "<用一句话说清",
    "<概括本周最实质的推进",
    "<实验协议、对照基线",
    "<请由 AI 从实际日志",
    "<用 1–3 句写清动作",
    "<用一句话说清核心发现或真实卡点>",
    "<推翻或修正了什么旧假设>",
    "<写明具体交付物、目标日期与主动舍弃项>",
    "<写明亲自核对了哪组数字、代码或日志",
)

LOCAL_ABS_PATH_RE = re.compile(
    r"(?:/Users/|/home/|/Volumes/|~/(?:OneDrive|Desktop|Documents|Downloads|Library|dev|Projects)\b"
    r"|[A-Za-z]:[/\\]|\bOneDrive\b)",
    re.IGNORECASE,
)

AI_DRAFT_LEAK_PHRASES = (
    "本地审阅草稿",
    "先生成 diff 并由用户确认",
    "请确认是否上传",
    "AI_DRAFT_PENDING_HUMAN",
)


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
        val = v.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        result[k.strip()] = val
    return result


def ensure_workspace(ws: Path) -> None:
    ws.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(ws, 0o700)
    except OSError:
        pass
    for sub in ("spokes", "digests"):
        (ws / sub).mkdir(parents=True, exist_ok=True)


def load_roster(ws: Path) -> List[Dict[str, Any]]:
    roster_file = ws / "roster.json"
    if not roster_file.is_file():
        return []
    data = json.loads(roster_file.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "repos" in data:
        return data["repos"]
    if isinstance(data, list):
        return data
    return []


def save_roster(ws: Path, entries: List[Dict[str, Any]]) -> None:
    roster_file = ws / "roster.json"
    payload = {
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "repos": entries,
    }
    roster_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def upsert_roster_entry(entries: List[Dict[str, Any]], full_name: str, local_source: Optional[str] = None) -> None:
    for item in entries:
        if item.get("full_name") == full_name:
            if local_source:
                item["local_source"] = local_source
            return
    entry: Dict[str, Any] = {
        "full_name": full_name,
        "added_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    if local_source:
        entry["local_source"] = local_source
    entries.append(entry)


def is_research_repo_name(name: str) -> bool:
    return name == "yao-research-memory" or name.startswith("yao-research-")


def accept_invites(ws: Path) -> int:
    if not shutil.which("gh"):
        raise RuntimeError("需要 gh CLI 才能自动接收 GitHub 邀请。")
    raw = run_cmd(["gh", "api", "user/repository_invitations"]).stdout
    invites = json.loads(raw)
    roster = load_roster(ws)
    accepted = 0
    for inv in invites:
        repo = inv.get("repository", {})
        name = repo.get("name", "")
        full_name = repo.get("full_name", "")
        if not is_research_repo_name(name):
            continue
        if not repo.get("private", False):
            print(f"[skip] 忽略非私有仓库邀请：{full_name}")
            continue
        inv_id = inv["id"]
        run_cmd(["gh", "api", "-X", "PATCH", f"user/repository_invitations/{inv_id}"])
        upsert_roster_entry(roster, full_name)
        accepted += 1
        print(f"[ok] 已接收私有库邀请并登记：{full_name}")
    save_roster(ws, roster)
    return accepted


def add_repos(ws: Path, repo_specs: List[str], skip_remote: bool) -> None:
    roster = load_roster(ws)
    for spec in repo_specs:
        if "=" in spec:
            full_name, local_src = spec.split("=", 1)
            full_name = full_name.strip()
            local_src = str(Path(local_src.strip()).expanduser().resolve())
            upsert_roster_entry(roster, full_name, local_source=local_src)
            print(f"[ok] 已登记本地测试仓库：{full_name} -> {local_src}")
            continue

        full_name = spec.strip()
        if "/" not in full_name:
            raise ValueError(f"仓库名格式无效（应为 owner/repo）：{full_name}")
        if not skip_remote:
            if not shutil.which("gh"):
                raise RuntimeError("需要 gh CLI 验证仓库私有性（或使用 --skip-remote）。")
            meta = json.loads(run_cmd(["gh", "api", f"repos/{full_name}"]).stdout)
            if not meta.get("private", False):
                raise RuntimeError(f"安全拦截：拒绝登记公开仓库 {full_name}！")
        upsert_roster_entry(roster, full_name)
        print(f"[ok] 已登记私有仓库：{full_name}")
    save_roster(ws, roster)


def spoke_dir_for(ws: Path, full_name: str) -> Path:
    return ws / "spokes" / full_name.replace("/", "__")


def sync_spokes(ws: Path, skip_remote: bool) -> List[Tuple[str, Path]]:
    roster = load_roster(ws)
    synced: List[Tuple[str, Path]] = []
    for entry in roster:
        full_name = entry["full_name"]
        target = spoke_dir_for(ws, full_name)
        local_src = entry.get("local_source")
        if local_src:
            src_path = Path(local_src)
            if (src_path / ".git").exists():
                if not target.exists():
                    run_cmd(["git", "clone", str(src_path), str(target)])
                else:
                    run_cmd(["git", "pull", "--ff-only"], cwd=target, check=False)
            else:
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(src_path, target)
            synced.append((full_name, target))
            continue

        if skip_remote:
            if target.exists():
                synced.append((full_name, target))
            continue

        clone_url = f"https://github.com/{full_name}.git"
        if not target.exists():
            run_cmd(["git", "clone", clone_url, str(target)])
        else:
            run_cmd(["git", "fetch", "origin"], cwd=target)
            branch_proc = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=target, check=False)
            branch = branch_proc.stdout.strip() or "main"
            if branch == "HEAD":
                branch = "main"
            run_cmd(["git", "pull", "--ff-only", "origin", branch], cwd=target, check=False)
        synced.append((full_name, target))
        print(f"[sync] 已同步：{full_name}")
    return synced


def load_advisor_prompt(hub_dir: Optional[Path], hub_repo: str, hub_branch: str, skip_remote: bool) -> str:
    rel = "protocol/prompts/advisor-preread.md"
    if hub_dir is not None and (hub_dir / rel).is_file():
        return (hub_dir / rel).read_text(encoding="utf-8")
    if not skip_remote:
        try:
            url = f"https://raw.githubusercontent.com/{hub_repo}/{hub_branch}/{rel}"
            req = urllib.request.Request(url, headers={"User-Agent": "yao-research-hub/3.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.read().decode("utf-8")
        except Exception:
            pass
    return BUILTIN_ADVISOR_PROMPT


def strip_markdown_chrome(text: str) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    lines = []
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


def extract_bullet_field(text: str, field_prefix: str, default: str = "-") -> str:
    pattern = re.compile(r"[-*]\s*\*\*" + re.escape(field_prefix) + r"[^*]*\*\*[：:]\s*(.+)")
    m = pattern.search(text)
    return m.group(1).strip().strip("`") if m else default


def parse_project_cards(spoke_dir: Path) -> List[Dict[str, str]]:
    cards: List[Dict[str, str]] = []
    proj_dir = spoke_dir / "projects"
    if not proj_dir.is_dir():
        return cards
    for p in sorted(proj_dir.glob("*.md")):
        txt = p.read_text(encoding="utf-8")
        title_m = re.search(r"^#\s+(?:项目活页卡[：:]\s*)?(.+)$", txt, re.MULTILINE)
        cards.append(
            {
                "file": p.name,
                "title": title_m.group(1).strip() if title_m else p.stem,
                "updated_at": extract_bullet_field(txt, "更新日期"),
                "venue": extract_bullet_field(txt, "目标会议"),
                "stage": extract_bullet_field(txt, "当前管线阶段"),
                "kill_criteria": extract_bullet_field(txt, "停止条件"),
                "raw": txt,
            }
        )
    return cards


def collect_recent_progress(spoke_dir: Path, limit: int = 5) -> Tuple[int, str, str]:
    """Read v2/v3 progress/*.md first, falling back to legacy logs/ and daily/."""
    entries: List[Path] = []
    for folder in ("progress", "logs", "daily"):
        d = spoke_dir / folder
        if d.is_dir():
            entries.extend(p for p in d.glob("*.md") if p.is_file())
    if not entries:
        return 0, "-", ""
    entries.sort(key=lambda p: p.name, reverse=True)
    latest = entries[0]
    snippets: List[str] = []
    for p in entries[:limit]:
        snippets.append(f"### {p.parent.name}/{p.name}\n{p.read_text(encoding='utf-8').strip()}")
    return len(entries), latest.stem, "\n\n".join(snippets)


def collect_week_artifacts(spoke_dir: Path, week: str) -> List[str]:
    art_dir = spoke_dir / "weekly" / week / "artifacts"
    if not art_dir.is_dir():
        return []
    rels = [
        str(p.relative_to(spoke_dir)).replace("\\", "/")
        for p in sorted(art_dir.rglob("*"))
        if p.is_file() and p.name != ".gitkeep"
    ]
    return rels


def collect_code_inventory(spoke_dir: Path) -> Tuple[int, List[str]]:
    code_dir = spoke_dir / "code"
    if not code_dir.is_dir():
        return 0, []
    files = [
        p for p in sorted(code_dir.rglob("*"))
        if p.is_file() and p.name != ".gitkeep"
    ]
    top_entries: List[str] = []
    for child in sorted(code_dir.iterdir()):
        if child.name == ".gitkeep":
            continue
        if child.is_dir():
            sub_cnt = sum(1 for f in child.rglob("*") if f.is_file() and f.name != ".gitkeep")
            top_entries.append(f"code/{child.name}/ ({sub_cnt} files)")
        else:
            top_entries.append(f"code/{child.name}")
    return len(files), top_entries[:15]


def collect_session_files(spoke_dir: Path, limit: int = 5) -> List[str]:
    sess_dir = spoke_dir / "sessions"
    if not sess_dir.is_dir():
        return []
    files = [
        p for p in sess_dir.glob("*.md") if p.is_file() and p.name != ".gitkeep"
    ]
    files.sort(key=lambda p: p.name, reverse=True)
    return [f"sessions/{p.name}" for p in files[:limit]]


def collect_recent_commits(spoke_dir: Path, limit: int = 5) -> List[str]:
    if not (spoke_dir / ".git").exists():
        return []
    proc = run_cmd(
        ["git", "log", f"-n{limit}", "--date=short", "--pretty=format:%h | %ad | %s"],
        cwd=spoke_dir,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def detect_audit_flags(
    has_evidence: bool,
    has_judgment: bool,
    has_report: bool,
    ev_txt: str,
    jd_txt: str,
    rep_txt: str,
) -> List[str]:
    flags: List[str] = []
    if (has_evidence or has_report) and not has_judgment:
        flags.append("missing_judgment")
    combined_docs = "\n".join(x for x in (ev_txt, jd_txt, rep_txt) if x)
    if combined_docs and LOCAL_ABS_PATH_RE.search(combined_docs):
        flags.append("has_local_abs_paths")
    if ev_txt and len(ev_txt) > 6000:
        flags.append("evidence_too_long")
    if combined_docs and any(ph in combined_docs for ph in AI_DRAFT_LEAK_PHRASES):
        flags.append("ai_draft_leak_phrase")
    return flags


def collect_student_record(full_name: str, spoke_dir: Path, week: str) -> Dict[str, Any]:
    profile_txt = (spoke_dir / "profile.md").read_text(encoding="utf-8") if (spoke_dir / "profile.md").is_file() else ""
    week_dir = spoke_dir / "weekly" / week
    meta_file = week_dir / "meta.yaml"
    rep_file = week_dir / "report.md"
    ev_file = week_dir / "evidence.md"
    jd_file = week_dir / "judgment.md"

    meta = parse_simple_yaml(meta_file.read_text(encoding="utf-8")) if meta_file.is_file() else {}
    rep_txt = rep_file.read_text(encoding="utf-8") if rep_file.is_file() else ""
    ev_txt = ev_file.read_text(encoding="utf-8") if ev_file.is_file() else ""
    jd_txt = jd_file.read_text(encoding="utf-8") if jd_file.is_file() else ""

    has_rep = len(strip_markdown_chrome(rep_txt)) >= 60 and not any(t in rep_txt for t in UNFILLED_TOKENS)
    has_ev = len(strip_markdown_chrome(ev_txt)) >= 60 and not any(t in ev_txt for t in UNFILLED_TOKENS)
    has_jd = len(strip_markdown_chrome(jd_txt)) >= 40 and not any(t in jd_txt for t in UNFILLED_TOKENS)
    log_count, latest_log_date, recent_progress_md = collect_recent_progress(spoke_dir)

    artifact_files = collect_week_artifacts(spoke_dir, week)
    code_count, code_top = collect_code_inventory(spoke_dir)
    session_files = collect_session_files(spoke_dir)
    recent_commits = collect_recent_commits(spoke_dir)
    audit_flags = detect_audit_flags(has_ev, has_jd, has_rep, ev_txt, jd_txt, rep_txt)

    if has_ev and has_jd and ("AI_DRAFT_PENDING_HUMAN" not in jd_txt) and meta.get("judgment_status") != "ai_draft":
        status = "submitted_human"
    elif has_rep or (has_ev and has_jd):
        status = "submitted_ai_mvp"
    elif has_ev:
        status = "missing_judgment"
    elif has_jd:
        status = "missing_evidence"
    elif log_count > 0 or code_count > 0 or artifact_files:
        status = "progress_only"
    else:
        status = "missing_all"

    gh_login = meta.get("github_login") or extract_bullet_field(profile_txt, "GitHub ID", "") or full_name.split("/")[0]
    prof_name = extract_bullet_field(profile_txt, "称呼", "") or extract_bullet_field(profile_txt, "姓名", "")
    if prof_name in {"待补充", "-", "姓名"}:
        prof_name = ""
    student_name = meta.get("student_name") or prof_name or gh_login
    if student_name in {"", "-", "姓名"}:
        student_name = gh_login

    role = meta.get("role") or extract_bullet_field(profile_txt, "身份", "-")
    prof_topic = extract_bullet_field(profile_txt, "主课题", "")
    if prof_topic in {"待补充", "-"}:
        prof_topic = ""
    topic = meta.get("topic") or prof_topic or "未指定课题"

    return {
        "repo": full_name,
        "spoke_local_path": str(spoke_dir.resolve()),
        "github_login": gh_login,
        "student_name": student_name,
        "role": role,
        "topic": topic,
        "group_partner": meta.get("group_partner", "-"),
        "meeting_request": meta.get("meeting_request", "none"),
        "blockers_summary": meta.get("blockers_summary", "-"),
        "help_needed_from": meta.get("help_needed_from", "-"),
        "help_needed_what": meta.get("help_needed_what", "-"),
        "has_product_task": meta.get("has_product_task", "no"),
        "log_count": log_count,
        "latest_log_date": latest_log_date,
        "submitted_at": meta.get("submitted_at", ""),
        "status": status,
        "has_report": has_rep,
        "has_evidence": has_ev,
        "has_judgment": has_jd,
        "artifact_files": artifact_files,
        "code_files_count": code_count,
        "code_top_entries": code_top,
        "session_files": session_files,
        "recent_commits": recent_commits,
        "audit_flags": audit_flags,
        "report_md": rep_txt,
        "evidence_md": ev_txt,
        "judgment_md": jd_txt,
        "recent_progress_md": recent_progress_md,
        "project_cards": parse_project_cards(spoke_dir),
    }


def render_safe_markdown_html(md_text: str) -> str:
    """Escape HTML first (XSS-safe), then format lightweight Markdown headings/lists/bold/code."""
    escaped = html.escape(md_text or "(暂无内容)")
    out_lines: List[str] = []
    in_list = False
    for raw in escaped.splitlines():
        line = raw.rstrip()
        if not line.strip():
            if in_list:
                out_lines.append("</ul>")
                in_list = False
            continue
        fmt = re.sub(r"`([^`]+)`", r"<code>\1</code>", line)
        fmt = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", fmt)

        if fmt.startswith("### "):
            if in_list:
                out_lines.append("</ul>")
                in_list = False
            out_lines.append(f"<h4>{fmt[4:]}</h4>")
        elif fmt.startswith("## "):
            if in_list:
                out_lines.append("</ul>")
                in_list = False
            out_lines.append(f"<h3>{fmt[3:]}</h3>")
        elif fmt.startswith("# "):
            if in_list:
                out_lines.append("</ul>")
                in_list = False
            out_lines.append(f"<h2>{fmt[2:]}</h2>")
        elif fmt.lstrip().startswith("- ") or re.match(r"^\s*[0-9]+\.\s+", fmt):
            if not in_list:
                out_lines.append("<ul>")
                in_list = True
            item_txt = re.sub(r"^\s*(?:-|[0-9]+\.)\s+", "", fmt)
            out_lines.append(f"<li>{item_txt}</li>")
        elif fmt.startswith("&lt;!--") and fmt.endswith("--&gt;"):
            continue
        else:
            if in_list:
                out_lines.append("</ul>")
                in_list = False
            out_lines.append(f"<p>{fmt}</p>")
    if in_list:
        out_lines.append("</ul>")
    return "\n".join(out_lines)


def status_badge(status: str) -> Tuple[str, str]:
    mapping = {
        "submitted_human": ("已齐备·双轨确认", "ok"),
        "submitted_ai_mvp": ("已提交周报", "ok"),
        "progress_only": ("已交今日进展（周报待交）", "warn"),
        "missing_judgment": ("缺本人判断页", "warn"),
        "missing_evidence": ("缺证据页", "warn"),
        "missing_all": ("尚未提交", "err"),
    }
    return mapping.get(status, (status, "warn"))


def build_team_digest_md(records: List[Dict[str, Any]], week: str) -> str:
    total = len(records)
    sub_human = sum(1 for r in records if r["status"] == "submitted_human")
    sub_ai = sum(1 for r in records if r["status"] == "submitted_ai_mvp")
    prog_only = sum(1 for r in records if r["status"] == "progress_only")
    miss_jd = sum(1 for r in records if r["status"] == "missing_judgment")
    missing = total - sub_human - sub_ai - prog_only - miss_jd

    lines = [
        f"# Yao Research Group 科研进展与代码审计汇总（{week}）",
        "",
        f"- **生成时间**：{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z')}",
        f"- **已登记**：{total} 人｜**周报完整**：{sub_human + sub_ai} 人｜**仅今日/代码**：{prog_only} 人｜**缺判断页**：{miss_jd} 人｜**未交**：{missing} 人",
        "",
        "## 1. 全员提交与可审计资产总表",
        "",
        "| 成员 | 主课题 | 状态 | 附件/代码 | 质量标旗 | 会议请求 | 本地克隆目录 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in records:
        label, _ = status_badge(r["status"])
        flags_str = ", ".join(r.get("audit_flags", [])) or "OK"
        asset_str = f"art:{len(r.get('artifact_files', []))} / code:{r.get('code_files_count', 0)}"
        lines.append(
            f"| {r['student_name']} | {r['topic']} | {label} | {asset_str} | {flags_str} | "
            f"`{r['meeting_request']}` | `{r.get('spoke_local_path', '-')}` |"
        )

    lines.extend(["", "## 2. 逐人核心判断与近期进展", ""])
    for r in records:
        lines.append(f"### {r['student_name']}（{r['topic']}｜请求：`{r['meeting_request']}`）")
        if r.get("spoke_local_path"):
            lines.append(f"- 本地源码与图表目录：`{r['spoke_local_path']}`")
        primary = r["judgment_md"].strip() or r["report_md"].strip() or r["recent_progress_md"].strip()
        lines.append(primary or "*(暂无文字提交)*")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def build_preread_input_md(records: List[Dict[str, Any]], week: str, advisor_prompt: str) -> str:
    lines = [
        f"# 导师 AI 会前预读与代码抽查输入包（{week}）",
        "",
        "## 系统提示词（Advisor Preread Prompt）",
        "",
        advisor_prompt.strip(),
        "",
        "---",
        "",
        f"## 全体成员科研材料与本地可审计仓库索引（共 {len(records)} 人）",
        "",
    ]
    for r in records:
        label, _ = status_badge(r["status"])
        lines.extend(
            [
                f"### 成员：{r['student_name']} (`{r['repo']}`)",
                f"- **状态**：{label}｜**身份**：{r['role']}｜**课题**：{r['topic']}｜**最近进展**：{r['latest_log_date']}（共 {r['log_count']} 份）",
                f"- **会议请求**：`{r['meeting_request']}`｜**阻塞**：{r['blockers_summary']}｜**求助**：{r['help_needed_from']} ({r['help_needed_what']})",
                f"- **本地克隆仓库路径（AI 可直接读代码/实验图表）**：`{r.get('spoke_local_path', '-')}`",
            ]
        )
        arts = r.get("artifact_files", [])
        if arts:
            sample = ", ".join(f"`{a}`" for a in arts[:20])
            more = f" 等共 {len(arts)} 个" if len(arts) > 20 else f"（共 {len(arts)} 个）"
            lines.append(f"- **本周可审计附件 (`artifacts/`)**：{sample}{more}")
        if r.get("code_files_count", 0) > 0:
            top_c = ", ".join(f"`{c}`" for c in r.get("code_top_entries", []))
            lines.append(f"- **代码库 (`code/`)**：共 {r['code_files_count']} 个文件（分布：{top_c}）")
        if r.get("session_files"):
            lines.append("- **近期讨论摘要 (`sessions/`)**：" + ", ".join(f"`{s}`" for s in r["session_files"]))
        if r.get("recent_commits"):
            lines.append("- **近期 Git 提交**：" + " ; ".join(r["recent_commits"][:3]))
        if r.get("audit_flags"):
            lines.append("- **质量自检标旗 (`audit_flags`)**：" + ", ".join(f"`{f}`" for f in r["audit_flags"]))
        lines.append("")

        if r["judgment_md"].strip():
            lines.extend(["#### 本人判断页 (`judgment.md`)", r["judgment_md"].strip(), ""])
        if r["evidence_md"].strip():
            lines.extend(["#### AI 证据页 (`evidence.md`)", r["evidence_md"].strip(), ""])
        if r["report_md"].strip():
            lines.extend(["#### 周报 (`report.md`)", r["report_md"].strip(), ""])
        if r["recent_progress_md"].strip():
            lines.extend(["#### 最近今日进展 (`progress/`)", r["recent_progress_md"].strip(), ""])
        lines.extend(["---", ""])
    return "\n".join(lines)


def build_advisor_html(records: List[Dict[str, Any]], week: str) -> str:
    total = len(records)
    sub_human = sum(1 for r in records if r["status"] == "submitted_human")
    sub_ai = sum(1 for r in records if r["status"] == "submitted_ai_mvp")
    prog_only = sum(1 for r in records if r["status"] == "progress_only")
    miss_jd = sum(1 for r in records if r["status"] == "missing_judgment")
    missing = total - sub_human - sub_ai - prog_only - miss_jd
    gen_time = html.escape(datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z"))
    week_esc = html.escape(week)

    overview_cards: List[str] = []
    for r in records:
        label, cls = status_badge(r["status"])
        primary_md = r["judgment_md"].strip() or r["report_md"].strip() or r["recent_progress_md"].strip()
        body_html = render_safe_markdown_html(primary_md) if primary_md else "<p class='muted'>（本周尚未提交文字内容）</p>"
        flags_html = " ".join(f"<span class='badge warn'>{html.escape(f)}</span>" for f in r.get("audit_flags", []))
        overview_cards.append(
            f"<article class='card'>"
            f"<div class='card-head'>"
            f"<div><strong>{html.escape(r['student_name'])}</strong> <span class='tag'>{html.escape(r['topic'])}</span> "
            f"<span class='tag'>art:{len(r.get('artifact_files', []))}</span> "
            f"<span class='tag'>code:{r.get('code_files_count', 0)}</span></div>"
            f"<div><span class='badge {cls}'>{html.escape(label)}</span> "
            f"<span class='tag'>请求: {html.escape(r['meeting_request'])}</span> {flags_html}</div>"
            f"</div>"
            f"<div class='meta-line'><strong>阻塞：</strong>{html.escape(r['blockers_summary'])} ｜ "
            f"<strong>本地目录：</strong><code>{html.escape(r.get('spoke_local_path', '-'))}</code></div>"
            f"<div class='card-body'>{body_html}</div>"
            f"</article>"
        )

    topics: Dict[str, List[Dict[str, Any]]] = {}
    for r in records:
        topics.setdefault(r["topic"], []).append(r)
    topic_blocks: List[str] = []
    for topic, grp in topics.items():
        names = ", ".join(html.escape(g["student_name"]) for g in grp)
        topic_blocks.append(f"<div class='card'><h3>{html.escape(topic)}</h3><p>参与成员：{names}</p></div>")

    archive_blocks: List[str] = []
    for r in records:
        label, cls = status_badge(r["status"])
        parts: List[str] = []
        if r["judgment_md"].strip():
            parts.append(f"<h4>本人判断页 (judgment.md)</h4><div class='card-body'>{render_safe_markdown_html(r['judgment_md'])}</div>")
        if r["report_md"].strip():
            parts.append(f"<h4>本周周报 (report.md)</h4><div class='card-body'>{render_safe_markdown_html(r['report_md'])}</div>")
        if r["evidence_md"].strip():
            parts.append(f"<details><summary>展开证据页 (evidence.md)</summary><div class='details-box'>{render_safe_markdown_html(r['evidence_md'])}</div></details>")
        if r.get("artifact_files"):
            art_list = "".join(f"<li><code>{html.escape(a)}</code></li>" for a in r["artifact_files"][:30])
            parts.append(f"<details><summary>本周附件 artifacts ({len(r['artifact_files'])})</summary><ul>{art_list}</ul></details>")
        if r.get("code_top_entries"):
            code_list = "".join(f"<li><code>{html.escape(c)}</code></li>" for c in r["code_top_entries"])
            parts.append(f"<details><summary>代码目录概览 ({r['code_files_count']} 文件)</summary><ul>{code_list}</ul></details>")
        if r["recent_progress_md"].strip():
            parts.append(f"<details open><summary>今日进展记录 ({r['log_count']} 份)</summary><div class='details-box'>{render_safe_markdown_html(r['recent_progress_md'])}</div></details>")
        archive_blocks.append(
            f"<article class='card'>"
            f"<div class='card-head'><div><strong>{html.escape(r['student_name'])}</strong> · <code>{html.escape(r['repo'])}</code></div>"
            f"<div><span class='badge {cls}'>{html.escape(label)}</span></div></div>"
            f"<p class='meta-line'>本地路径：<code>{html.escape(r.get('spoke_local_path', '-'))}</code> ｜ 提交时间：{html.escape(r['submitted_at'] or '-')}</p>"
            f"{''.join(parts) or '<p class=muted>暂无记录</p>'}"
            f"</article>"
        )

    status_rows: List[str] = []
    for r in records:
        label, cls = status_badge(r["status"])
        flags_txt = ", ".join(r.get("audit_flags", [])) or "OK"
        status_rows.append(
            "<tr>"
            f"<td>{html.escape(r['student_name'])}</td>"
            f"<td>{html.escape(r['topic'])}</td>"
            f"<td><span class='badge {cls}'>{html.escape(label)}</span></td>"
            f"<td>art:{len(r.get('artifact_files', []))} / code:{r.get('code_files_count', 0)}</td>"
            f"<td>{html.escape(flags_txt)}</td>"
            f"<td><code>{html.escape(r['meeting_request'])}</code></td>"
            f"<td><code>{html.escape(r['repo'])}</code></td>"
            "</tr>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Yao Research Group 科研与代码汇总 · {week_esc}</title>
<style>
  :root {{ --bg:#f8fafc; --card:#ffffff; --text:#0f172a; --muted:#64748b; --border:#e2e8f0; --accent:#2563eb; }}
  * {{ box-sizing:border-box; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; background:var(--bg); color:var(--text); margin:0; padding:24px; line-height:1.6; }}
  .wrap {{ max-width:1120px; margin:0 auto; }}
  header {{ background:var(--card); border:1px solid var(--border); border-radius:12px; padding:20px 24px; margin-bottom:16px; }}
  h1 {{ margin:0 0 8px 0; font-size:22px; }}
  .stats {{ color:var(--muted); font-size:14px; }}
  .tabs {{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:16px; }}
  .tab-btn {{ border:1px solid var(--border); background:var(--card); padding:8px 14px; border-radius:8px; cursor:pointer; font-size:14px; font-weight:600; color:var(--text); }}
  .tab-btn.active {{ background:var(--accent); color:#fff; border-color:var(--accent); }}
  .panel {{ display:none; }}
  .panel.active {{ display:block; }}
  .card {{ background:var(--card); border:1px solid var(--border); border-radius:10px; padding:16px 20px; margin-bottom:14px; }}
  .card-head {{ display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px; margin-bottom:8px; }}
  .meta-line {{ font-size:13px; color:var(--muted); margin:6px 0 10px 0; }}
  .tag {{ display:inline-block; background:#f1f5f9; color:#334155; font-size:12px; padding:2px 8px; border-radius:999px; }}
  .badge {{ display:inline-block; font-size:12px; font-weight:600; padding:2px 8px; border-radius:999px; }}
  .badge.ok {{ background:#dcfce7; color:#166534; }}
  .badge.warn {{ background:#fef9c3; color:#854d0e; }}
  .badge.err {{ background:#fee2e2; color:#991b1b; }}
  table {{ width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--border); border-radius:10px; overflow:hidden; margin-bottom:16px; font-size:13px; }}
  th, td {{ text-align:left; padding:10px 12px; border-bottom:1px solid var(--border); vertical-align:top; }}
  th {{ background:#f1f5f9; font-weight:600; }}
  details {{ margin-top:10px; border:1px solid var(--border); border-radius:8px; padding:8px 12px; background:#f8fafc; }}
  summary {{ cursor:pointer; font-weight:600; font-size:14px; }}
  .details-box {{ margin-top:8px; }}
  code {{ background:#f1f5f9; padding:1px 5px; border-radius:4px; font-size:12px; }}
  .muted {{ color:var(--muted); }}
  @media print {{
    body {{ background:#fff; padding:0; }}
    .tabs {{ display:none; }}
    .panel {{ display:block !important; margin-bottom:24px; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Yao Research Group 科研与代码审计汇总 · {week_esc}</h1>
    <div class="stats">生成时间：{gen_time} ｜ 已登记：{total} 人 ｜ 周报完整：{sub_human + sub_ai} 人 ｜ 仅进展/代码：{prog_only} 人 ｜ 缺判断页：{miss_jd} 人 ｜ 未交：{missing} 人</div>
  </header>

  <div class="tabs">
    <button class="tab-btn active" onclick="showTab('tab-judgments', this)">1. 本周判断与速览</button>
    <button class="tab-btn" onclick="showTab('tab-topics', this)">2. 按课题分布</button>
    <button class="tab-btn" onclick="showTab('tab-archives', this)">3. 逐人材料与代码</button>
    <button class="tab-btn" onclick="showTab('tab-status', this)">4. 状态与自检总表</button>
  </div>

  <section id="tab-judgments" class="panel active">
    {''.join(overview_cards) or "<p class='muted'>暂无已同步仓库数据。</p>"}
  </section>

  <section id="tab-topics" class="panel">
    {''.join(topic_blocks)}
  </section>

  <section id="tab-archives" class="panel">
    {''.join(archive_blocks)}
  </section>

  <section id="tab-status" class="panel">
    <table>
      <thead><tr><th>成员</th><th>主课题</th><th>状态</th><th>附件/代码</th><th>质量标旗</th><th>会议请求</th><th>仓库</th></tr></thead>
      <tbody>{''.join(status_rows)}</tbody>
    </table>
  </section>
</div>
<script>
function showTab(id, btn) {{
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  if (btn) btn.classList.add('active');
}}
</script>
</body>
</html>
"""


def generate_digests(
    ws: Path,
    week: str,
    hub_dir: Optional[Path],
    hub_repo: str,
    hub_branch: str,
    skip_remote: bool,
) -> Path:
    roster = load_roster(ws)
    records: List[Dict[str, Any]] = []
    for entry in roster:
        full_name = entry["full_name"]
        sdir = spoke_dir_for(ws, full_name)
        if not sdir.is_dir():
            records.append(
                {
                    "repo": full_name,
                    "spoke_local_path": str(sdir),
                    "github_login": full_name.split("/")[0],
                    "student_name": full_name.split("/")[-1],
                    "role": "-",
                    "topic": "未同步",
                    "group_partner": "-",
                    "meeting_request": "none",
                    "blockers_summary": "仓库尚未同步",
                    "help_needed_from": "-",
                    "help_needed_what": "-",
                    "has_product_task": "no",
                    "log_count": 0,
                    "latest_log_date": "-",
                    "submitted_at": "",
                    "status": "missing_all",
                    "has_report": False,
                    "has_evidence": False,
                    "has_judgment": False,
                    "artifact_files": [],
                    "code_files_count": 0,
                    "code_top_entries": [],
                    "session_files": [],
                    "recent_commits": [],
                    "audit_flags": [],
                    "report_md": "",
                    "evidence_md": "",
                    "judgment_md": "",
                    "recent_progress_md": "",
                    "project_cards": [],
                }
            )
        else:
            records.append(collect_student_record(full_name, sdir, week))

    out_dir = ws / "digests" / week
    out_dir.mkdir(parents=True, exist_ok=True)

    sub_human = sum(1 for r in records if r["status"] == "submitted_human")
    sub_ai = sum(1 for r in records if r["status"] == "submitted_ai_mvp")
    prog_only = sum(1 for r in records if r["status"] == "progress_only")
    miss_jd = sum(1 for r in records if r["status"] == "missing_judgment")
    status_payload = {
        "week": week,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "summary": {
            "total": len(records),
            "submitted_total": sub_human + sub_ai,
            "submitted_human": sub_human,
            "submitted_ai_mvp": sub_ai,
            "progress_only": prog_only,
            "missing_judgment": miss_jd,
            "missing_evidence": sum(1 for r in records if r["status"] == "missing_evidence"),
            "missing_all": sum(1 for r in records if r["status"] == "missing_all"),
        },
        "members": [
            {
                k: v
                for k, v in r.items()
                if k not in {"report_md", "evidence_md", "judgment_md", "recent_progress_md", "project_cards"}
            }
            for r in records
        ],
    }
    (out_dir / "status.json").write_text(
        json.dumps(status_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "team-digest.md").write_text(build_team_digest_md(records, week), encoding="utf-8")
    advisor_prompt = load_advisor_prompt(hub_dir, hub_repo, hub_branch, skip_remote)
    (out_dir / "advisor-preread-input.md").write_text(
        build_preread_input_md(records, week, advisor_prompt), encoding="utf-8"
    )
    (out_dir / "advisor-report.html").write_text(build_advisor_html(records, week), encoding="utf-8")
    print(f"[ok] 已生成 {week} 全部汇总产物：{out_dir}")
    return out_dir


def parse_args(
    argv: Optional[List[str]] = None,
    default_workspace: str = "~/.yao-research-advisor",
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导师汇总与代码抽查端：收邀、拉取私有库、生成预读与HTML")
    parser.add_argument("--workspace", default=default_workspace, help="导师私有聚合工作区（0700）")
    parser.add_argument("--accept-invites", action="store_true", help="自动接收 GitHub 上 yao-research-* 私有库协作者邀请")
    parser.add_argument("--add-repo", action="append", default=[], help="手动登记仓库（owner/repo 或 owner/repo=本地路径）")
    parser.add_argument("--sync", action="store_true", help="批量 clone/pull 已登记的私有仓库")
    parser.add_argument("--digest", action="store_true", help="生成 status.json / team-digest.md / preread / HTML")
    parser.add_argument("--week", default=current_iso_week(), help="汇总周次（默认当前 ISO 周）")
    parser.add_argument("--hub-dir", default=None, help="本地协议中心目录")
    parser.add_argument("--hub-repo", default=DEFAULT_HUB_REPO, help="公共协议中心仓库")
    parser.add_argument("--hub-branch", default=DEFAULT_HUB_BRANCH, help="公共协议中心分支")
    parser.add_argument("--skip-remote", action="store_true", help="离线模式（跳过 GitHub API 与远端 pull）")
    return parser.parse_args(argv)


def run_cli(
    argv: Optional[List[str]] = None,
    default_workspace: str = "~/.yao-research-advisor",
) -> int:
    args = parse_args(argv, default_workspace=default_workspace)
    ws = Path(args.workspace).expanduser().resolve()
    ensure_workspace(ws)
    hub_dir = Path(args.hub_dir).expanduser().resolve() if args.hub_dir else None

    if args.accept_invites:
        accept_invites(ws)
    if args.add_repo:
        add_repos(ws, args.add_repo, args.skip_remote)
    if args.sync:
        sync_spokes(ws, args.skip_remote)
    if args.digest:
        generate_digests(ws, args.week, hub_dir, args.hub_repo, args.hub_branch, args.skip_remote)

    if not any([args.accept_invites, args.add_repo, args.sync, args.digest]):
        print(f"[info] 导师工作区就绪：{ws}（使用 --help 查看全部参数）")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    return run_cli(argv, default_workspace="~/.yao-research-advisor")


if __name__ == "__main__":
    raise SystemExit(main())
