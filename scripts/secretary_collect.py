#!/usr/bin/env python3
"""Secretary & advisor collector: accept private repo invites, sync spokes, and build digests + HTML report.

Zero third-party dependencies (Python 3.9+ stdlib only).
All file I/O explicitly specifies UTF-8 encoding.
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

BUILTIN_ADVISOR_PROMPT = """阅读这名学生的 evidence.md（证据页）、judgment.md（判断页）和 meta.yaml，输出：
1. 3 行摘要：做了什么、证据是否充分、下一步是否清楚；
2. 判断页每条结论在证据页是否有支持（有支持/部分支持/无支持）并指明位置；
3. 学生没回答但应该回答的缺口；
4. 最多 3 个最值得讨论的问题，按重要性排序（写成问题，不下结论）；
5. 建议路由（不需要开会/小组讨论/1:1/周四深度决策/周六产品评审）及理由。
不写表扬与客套话，不确定处标明 [不确定]。
读完全部学生后，另外输出：课题之间的重复/冲突/共同阻塞，以及按紧急程度排序的本周需要老师关注名单。"""

UNFILLED_JUDGMENT_TOKENS = ("<请用本人语言填写>", "<用一句话说清")


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


def accept_invites(ws: Path, prefix: str = "yao-research-") -> int:
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
        if not name.startswith(prefix):
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
    safe = full_name.replace("/", "__")
    return ws / "spokes" / safe


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
            req = urllib.request.Request(url, headers={"User-Agent": "yao-research-hub/1.0"})
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
        if s.startswith("- **提交人**") or s.startswith("- **填写要求**"):
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


def collect_student_record(full_name: str, spoke_dir: Path, week: str) -> Dict[str, Any]:
    profile_txt = (spoke_dir / "profile.md").read_text(encoding="utf-8") if (spoke_dir / "profile.md").is_file() else ""
    week_dir = spoke_dir / "weekly" / week
    meta_file = week_dir / "meta.yaml"
    ev_file = week_dir / "evidence.md"
    jd_file = week_dir / "judgment.md"
    prod_file = week_dir / "product.md"

    meta = parse_simple_yaml(meta_file.read_text(encoding="utf-8")) if meta_file.is_file() else {}
    ev_txt = ev_file.read_text(encoding="utf-8") if ev_file.is_file() else ""
    jd_txt = jd_file.read_text(encoding="utf-8") if jd_file.is_file() else ""
    prod_txt = prod_file.read_text(encoding="utf-8") if prod_file.is_file() else ""

    ev_body = strip_markdown_chrome(ev_txt)
    jd_body = strip_markdown_chrome(jd_txt)
    jd_unfilled = any(tok in jd_txt for tok in UNFILLED_JUDGMENT_TOKENS)

    has_ev = len(ev_body) >= 80
    has_jd = (len(jd_body) >= 40) and not jd_unfilled

    if has_ev and has_jd:
        status = "submitted"
    elif has_ev and not has_jd:
        status = "missing_judgment"
    elif (not has_ev) and has_jd:
        status = "missing_evidence"
    else:
        status = "missing_all"

    student_name = meta.get("student_name") or extract_bullet_field(profile_txt, "姓名", full_name.split("/")[-1])
    role = meta.get("role") or extract_bullet_field(profile_txt, "身份", "-")
    topic = meta.get("topic") or extract_bullet_field(profile_txt, "主课题", "未分类")
    code_repos = meta.get("code_repos") or extract_bullet_field(profile_txt, "关联代码仓库", "-")

    return {
        "repo": full_name,
        "student_name": student_name,
        "role": role,
        "topic": topic,
        "group_partner": meta.get("group_partner", "-"),
        "meeting_request": meta.get("meeting_request", "none"),
        "blockers_summary": meta.get("blockers_summary", "-"),
        "help_needed_from": meta.get("help_needed_from", "-"),
        "help_needed_what": meta.get("help_needed_what", "-"),
        "has_product_task": meta.get("has_product_task", "no"),
        "code_repos": code_repos,
        "submitted_at": meta.get("submitted_at", ""),
        "status": status,
        "has_evidence": has_ev,
        "has_judgment": has_jd,
        "has_product_update": bool(prod_txt.strip()),
        "evidence_md": ev_txt,
        "judgment_md": jd_txt,
        "product_md": prod_txt,
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
        # Inline formatting on escaped text
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
        "submitted": ("已齐备提交", "ok"),
        "missing_judgment": ("缺本人判断页（视为未交）", "warn"),
        "missing_evidence": ("缺 AI 证据页", "warn"),
        "missing_all": ("尚未提交", "err"),
    }
    return mapping.get(status, (status, "warn"))


def build_team_digest_md(records: List[Dict[str, Any]], week: str) -> str:
    total = len(records)
    submitted = sum(1 for r in records if r["status"] == "submitted")
    missing_jd = sum(1 for r in records if r["status"] == "missing_judgment")
    missing_all = sum(1 for r in records if r["status"] in ("missing_all", "missing_evidence"))

    lines = [
        f"# Yao Research Group 周报汇总（{week}）",
        "",
        f"- **生成时间**：{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z')}",
        f"- **应交人数**：{total}｜**完整提交**：{submitted}｜**缺判断页（按未交）**：{missing_jd}｜**未交/缺证据**：{missing_all}",
        "",
        "## 1. 全员提交与会议路由总表",
        "",
        "| 姓名 | 身份 | 主课题 | 状态 | 会议请求 | 阻塞摘要 | 需协助方 | 私有库 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in records:
        label, _ = status_badge(r["status"])
        lines.append(
            f"| {r['student_name']} | {r['role']} | {r['topic']} | {label} | "
            f"`{r['meeting_request']}` | {r['blockers_summary']} | {r['help_needed_from']} | `{r['repo']}` |"
        )

    lines.extend(["", "## 2. 按课题与论文管线汇总", ""])
    topics: Dict[str, List[Dict[str, Any]]] = {}
    for r in records:
        topics.setdefault(r["topic"], []).append(r)

    for topic, group in topics.items():
        members = "、".join(g["student_name"] for g in group)
        lines.append(f"### 课题：{topic}（{members}）")
        for g in group:
            for c in g["project_cards"]:
                lines.append(
                    f"- **{g['student_name']} / {c['title']}**：阶段 `{c['stage']}`｜目标 `{c['venue']}`｜停止条件：{c['kill_criteria']}（更新：{c['updated_at']}）"
                )
        lines.append("")

    lines.extend(["## 3. 全体学生判断页速览", ""])
    for r in records:
        lines.append(f"### {r['student_name']}（{r['topic']}｜请求：`{r['meeting_request']}`）")
        lines.append(r["judgment_md"].strip() or "*(未提交判断页)*")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def build_preread_input_md(records: List[Dict[str, Any]], week: str, advisor_prompt: str) -> str:
    lines = [
        f"# 导师 / 秘书 AI 会前预读输入包（{week}）",
        "",
        "## 系统提示词（Advisor Preread Prompt）",
        "",
        advisor_prompt.strip(),
        "",
        "---",
        "",
        f"## 全体成员本周原始周报材料（共 {len(records)} 人）",
        "",
    ]
    for r in records:
        label, _ = status_badge(r["status"])
        lines.extend(
            [
                f"### 学生：{r['student_name']} (`{r['repo']}`)",
                f"- 状态：{label}｜身份：{r['role']}｜课题：{r['topic']}｜结对：{r['group_partner']}",
                f"- 会议请求：`{r['meeting_request']}`｜阻塞：{r['blockers_summary']}｜求助：{r['help_needed_from']} ({r['help_needed_what']})",
                f"- 关联代码：{r['code_repos']}",
                "",
                "#### 本人判断页 (`judgment.md`)",
                r["judgment_md"].strip() or "*(未提交)*",
                "",
                "#### AI 证据页 (`evidence.md`)",
                r["evidence_md"].strip() or "*(未提交)*",
                "",
            ]
        )
        if r["project_cards"]:
            lines.append("#### 项目活页卡摘要")
            for c in r["project_cards"]:
                lines.append(f"- **{c['title']}** (阶段: {c['stage']} | 目标: {c['venue']} | 停止条件: {c['kill_criteria']})")
            lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def build_advisor_html(records: List[Dict[str, Any]], week: str) -> str:
    total = len(records)
    submitted = sum(1 for r in records if r["status"] == "submitted")
    missing = total - submitted
    gen_time = html.escape(datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z"))
    week_esc = html.escape(week)

    # Tab 1: Judgments
    judgment_cards_html: List[str] = []
    for r in records:
        label, cls = status_badge(r["status"])
        jd_body = render_safe_markdown_html(r["judgment_md"]) if r["has_judgment"] else "<p class='muted'>（尚未提交有效判断页）</p>"
        judgment_cards_html.append(
            f"<article class='card'>"
            f"<div class='card-head'>"
            f"<div><strong>{html.escape(r['student_name'])}</strong> <span class='tag'>{html.escape(r['role'])}</span> "
            f"<span class='tag'>{html.escape(r['topic'])}</span></div>"
            f"<div><span class='badge {cls}'>{html.escape(label)}</span> "
            f"<span class='tag'>会议请求: {html.escape(r['meeting_request'])}</span></div>"
            f"</div>"
            f"<div class='meta-line'><strong>阻塞：</strong>{html.escape(r['blockers_summary'])} ｜ "
            f"<strong>求助：</strong>{html.escape(r['help_needed_from'])} ({html.escape(r['help_needed_what'])})</div>"
            f"<div class='card-body'>{jd_body}</div>"
            f"</article>"
        )

    # Tab 2: Topics & Pipeline
    topics: Dict[str, List[Dict[str, Any]]] = {}
    for r in records:
        topics.setdefault(r["topic"], []).append(r)
    pipeline_rows: List[str] = []
    topic_blocks: List[str] = []
    for topic, grp in topics.items():
        names = ", ".join(html.escape(g["student_name"]) for g in grp)
        topic_blocks.append(f"<div class='card'><h3>{html.escape(topic)}</h3><p>参与人：{names}</p></div>")
        for g in grp:
            for c in g["project_cards"]:
                pipeline_rows.append(
                    "<tr>"
                    f"<td>{html.escape(g['student_name'])}</td>"
                    f"<td>{html.escape(c['title'])}</td>"
                    f"<td>{html.escape(c['stage'])}</td>"
                    f"<td>{html.escape(c['venue'])}</td>"
                    f"<td>{html.escape(c['kill_criteria'])}</td>"
                    f"<td>{html.escape(c['updated_at'])}</td>"
                    "</tr>"
                )

    # Tab 3: Full Archives
    archive_blocks: List[str] = []
    for r in records:
        label, cls = status_badge(r["status"])
        ev_html = render_safe_markdown_html(r["evidence_md"])
        jd_html = render_safe_markdown_html(r["judgment_md"])
        prod_html = (
            f"<details><summary>产品进展更新 (product.md)</summary><div class='details-box'>{render_safe_markdown_html(r['product_md'])}</div></details>"
            if r["has_product_update"]
            else ""
        )
        cards_html = "".join(
            f"<details><summary>项目活页卡：{html.escape(c['title'])} ({html.escape(c['stage'])})</summary>"
            f"<div class='details-box'>{render_safe_markdown_html(c['raw'])}</div></details>"
            for c in r["project_cards"]
        )
        archive_blocks.append(
            f"<article class='card'>"
            f"<div class='card-head'><div><strong>{html.escape(r['student_name'])}</strong> · <code>{html.escape(r['repo'])}</code></div>"
            f"<div><span class='badge {cls}'>{html.escape(label)}</span></div></div>"
            f"<p class='meta-line'>代码库：<code>{html.escape(r['code_repos'])}</code> ｜ 提交时间：{html.escape(r['submitted_at'] or '-')}</p>"
            f"<h4>本人判断页</h4><div class='card-body'>{jd_html}</div>"
            f"<details><summary>展开 AI 证据页 (evidence.md)</summary><div class='details-box'>{ev_html}</div></details>"
            f"{prod_html}{cards_html}"
            f"</article>"
        )

    # Tab 4: Status Table
    status_rows: List[str] = []
    for r in records:
        label, cls = status_badge(r["status"])
        status_rows.append(
            "<tr>"
            f"<td>{html.escape(r['student_name'])}</td>"
            f"<td>{html.escape(r['topic'])}</td>"
            f"<td><span class='badge {cls}'>{html.escape(label)}</span></td>"
            f"<td><code>{html.escape(r['meeting_request'])}</code></td>"
            f"<td>{html.escape(r['blockers_summary'])}</td>"
            f"<td>{html.escape(r['submitted_at'] or '-')}</td>"
            f"<td><code>{html.escape(r['repo'])}</code></td>"
            "</tr>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Yao Research Group 周报汇总 · {week_esc}</title>
<style>
  :root {{ --bg:#f8fafc; --card:#ffffff; --text:#0f172a; --muted:#64748b; --border:#e2e8f0; --accent:#2563eb; }}
  * {{ box-sizing:border-box; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; background:var(--bg); color:var(--text); margin:0; padding:24px; line-height:1.6; }}
  .wrap {{ max-width:1080px; margin:0 auto; }}
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
    details {{ open:true; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Yao Research Group 科研周报汇报 · {week_esc}</h1>
    <div class="stats">生成时间：{gen_time} ｜ 已登记：{total} 人 ｜ 完整提交：{submitted} 人 ｜ 缺失/未交：{missing} 人</div>
  </header>

  <div class="tabs">
    <button class="tab-btn active" onclick="showTab('tab-judgments', this)">1. 全部判断页速览</button>
    <button class="tab-btn" onclick="showTab('tab-topics', this)">2. 按课题与论文管线</button>
    <button class="tab-btn" onclick="showTab('tab-archives', this)">3. 逐人完整档案（含证据页）</button>
    <button class="tab-btn" onclick="showTab('tab-status', this)">4. 提交状态与催收表</button>
  </div>

  <section id="tab-judgments" class="panel active">
    {''.join(judgment_cards_html) or "<p class='muted'>暂无已同步仓库数据。</p>"}
  </section>

  <section id="tab-topics" class="panel">
    <h2>论文管线与停止条件看板</h2>
    <table>
      <thead><tr><th>姓名</th><th>项目卡</th><th>阶段</th><th>目标会议/节点</th><th>停止条件</th><th>更新</th></tr></thead>
      <tbody>{''.join(pipeline_rows) or "<tr><td colspan='6' class='muted'>暂无项目活页卡</td></tr>"}</tbody>
    </table>
    <h2>按课题分布</h2>
    {''.join(topic_blocks)}
  </section>

  <section id="tab-archives" class="panel">
    {''.join(archive_blocks)}
  </section>

  <section id="tab-status" class="panel">
    <table>
      <thead><tr><th>姓名</th><th>主课题</th><th>状态</th><th>会议请求</th><th>阻塞</th><th>提交时间</th><th>仓库</th></tr></thead>
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
                    "student_name": full_name.split("/")[-1],
                    "role": "-",
                    "topic": "未同步",
                    "group_partner": "-",
                    "meeting_request": "none",
                    "blockers_summary": "仓库尚未同步",
                    "help_needed_from": "-",
                    "help_needed_what": "-",
                    "has_product_task": "no",
                    "code_repos": "-",
                    "submitted_at": "",
                    "status": "missing_all",
                    "has_evidence": False,
                    "has_judgment": False,
                    "has_product_update": False,
                    "evidence_md": "",
                    "judgment_md": "",
                    "product_md": "",
                    "project_cards": [],
                }
            )
        else:
            records.append(collect_student_record(full_name, sdir, week))

    out_dir = ws / "digests" / week
    out_dir.mkdir(parents=True, exist_ok=True)

    status_payload = {
        "week": week,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "summary": {
            "total": len(records),
            "submitted": sum(1 for r in records if r["status"] == "submitted"),
            "missing_judgment": sum(1 for r in records if r["status"] == "missing_judgment"),
            "missing_evidence": sum(1 for r in records if r["status"] == "missing_evidence"),
            "missing_all": sum(1 for r in records if r["status"] == "missing_all"),
        },
        "members": [
            {
                k: v
                for k, v in r.items()
                if k not in {"evidence_md", "judgment_md", "product_md", "project_cards"}
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


def push_review(ws: Path, repo_full_name: str, week: str, file_path: Path, kind: str, skip_remote: bool) -> None:
    if not file_path.is_file():
        raise FileNotFoundError(f"找不到反馈文件：{file_path}")
    sdir = spoke_dir_for(ws, repo_full_name)
    if not sdir.is_dir():
        raise RuntimeError(f"本地未找到已同步的仓库目录：{sdir}，请先执行 --sync")
    if not skip_remote:
        meta = json.loads(run_cmd(["gh", "api", f"repos/{repo_full_name}"]).stdout)
        if not meta.get("private", False):
            raise RuntimeError(f"安全拦截：拒绝向非私有仓库 {repo_full_name} 写入批示！")

    dest = sdir / "reviews" / f"{week}-{kind}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(file_path.read_text(encoding="utf-8"), encoding="utf-8")
    run_cmd(["git", "add", str(dest.relative_to(sdir))], cwd=sdir)
    if run_cmd(["git", "status", "--porcelain"], cwd=sdir).stdout.strip():
        run_cmd(["git", "commit", "-m", f"review({week}): add {kind}"], cwd=sdir)
        if not skip_remote:
            branch = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=sdir).stdout.strip() or "main"
            run_cmd(["git", "push", "origin", branch], cwd=sdir)
        print(f"[ok] 已写入并提交批示：{dest}")
    else:
        print(f"[ok] 批示内容无变化：{dest}")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="秘书与导师汇总端：自动收邀、拉取私有库、生成多视图周报与HTML")
    parser.add_argument("--workspace", default="~/.yao-research-secretary", help="秘书私有聚合工作区（0700）")
    parser.add_argument("--accept-invites", action="store_true", help="自动接收 GitHub 上 yao-research-* 私有库邀请")
    parser.add_argument("--add-repo", action="append", default=[], help="手动登记仓库（owner/repo 或 owner/repo=本地路径）")
    parser.add_argument("--sync", action="store_true", help="批量 clone/pull 已登记的私有仓库")
    parser.add_argument("--digest", action="store_true", help="生成 status.json / team-digest.md / preread / HTML")
    parser.add_argument("--week", default=current_iso_week(), help="汇总周次（默认当前 ISO 周）")
    parser.add_argument("--hub-dir", default=None, help="本地协议中心目录")
    parser.add_argument("--hub-repo", default=DEFAULT_HUB_REPO, help="公共协议中心仓库")
    parser.add_argument("--hub-branch", default=DEFAULT_HUB_BRANCH, help="公共协议中心分支")
    parser.add_argument("--push-review", action="store_true", help="回写导师批示或 AI 预读反馈到学生私有库")
    parser.add_argument("--repo", default=None, help="回写目标仓库 owner/repo")
    parser.add_argument("--file", default=None, help="待回写的 Markdown 文件路径")
    parser.add_argument("--kind", default="advisor", choices=["ai-review", "advisor"], help="批示类型")
    parser.add_argument("--skip-remote", action="store_true", help="离线模式（跳过 GitHub API 与远端 pull/push）")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
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
    if args.push_review:
        if not args.repo or not args.file:
            raise ValueError("--push-review 需要同时提供 --repo 和 --file")
        push_review(ws, args.repo, args.week, Path(args.file).expanduser().resolve(), args.kind, args.skip_remote)

    if not any([args.accept_invites, args.add_repo, args.sync, args.digest, args.push_review]):
        print(f"[info] 工作区就绪：{ws}（使用 --help 查看全部子命令）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
