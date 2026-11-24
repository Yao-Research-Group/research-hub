"""Offline unit tests for Yao Research Hub v1.1 (yao_sync.py + secretary_collect.py + legacy compat)."""
from __future__ import annotations

import io
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

HUB_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = HUB_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import secretary_collect  # noqa: E402
import yao_sync  # noqa: E402


class HubV11Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="yao-hub-v11-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_zero_prompt_init_env_detection_and_idempotent_rules(self) -> None:
        fake_home = self.tmp / "home"
        (fake_home / ".claude").mkdir(parents=True)
        (fake_home / ".codex").mkdir(parents=True)
        (fake_home / ".cursor").mkdir(parents=True)
        claude_md = fake_home / ".claude" / "CLAUDE.md"
        claude_md.write_text("# User existing Claude rules\n", encoding="utf-8")

        repo_dir = self.tmp / "student_repo"
        proj_dir = self.tmp / "proj"
        proj_dir.mkdir(parents=True)

        prev_home = os.environ.get("HOME")
        os.environ["HOME"] = str(fake_home)
        try:
            # 0) Calling init without --project-dir / --global / --no-agent-md MUST fail (rc=2) and touch nothing
            rc_missing_scope = yao_sync.main(
                [
                    "init",
                    "--repo-dir", str(repo_dir),
                    "--hub-dir", str(HUB_DIR),
                    "--skip-remote",
                ]
            )
            self.assertEqual(rc_missing_scope, 2)
            self.assertEqual(claude_md.read_text(encoding="utf-8"), "# User existing Claude rules\n")
            self.assertFalse((fake_home / ".codex" / "AGENTS.md").exists())

            # 1) Explicit --global init
            rc = yao_sync.main(
                [
                    "init",
                    "--global",
                    "--repo-dir", str(repo_dir),
                    "--hub-dir", str(HUB_DIR),
                    "--skip-remote",
                    "--now", "2026-11-24T13:00:00+08:00",
                ]
            )
            self.assertEqual(rc, 0)
            self.assertTrue((repo_dir / ".yao-notebook.json").is_file())
            self.assertTrue((repo_dir / "logs").is_dir())
            self.assertTrue((repo_dir / "projects").is_dir())
            self.assertTrue((repo_dir / "weekly").is_dir())

            nb = json.loads((repo_dir / ".yao-notebook.json").read_text(encoding="utf-8"))
            self.assertEqual(nb["schema_version"], 2)
            self.assertIn("claude", nb["env"]["ai_tools"])
            self.assertIn("codex", nb["env"]["ai_tools"])

            c_txt = claude_md.read_text(encoding="utf-8")
            self.assertIn("# User existing Claude rules", c_txt)
            self.assertIn(yao_sync.BEGIN_MARKER, c_txt)
            self.assertIn("适用范围", c_txt)
            self.assertTrue((fake_home / ".codex" / "AGENTS.md").is_file())
            self.assertTrue((fake_home / ".cursor" / "rules" / "yao-research-hub.mdc").is_file())

            # Re-init with --global is idempotent (exactly 1 marker block)
            rc2 = yao_sync.main(
                [
                    "init",
                    "--global",
                    "--repo-dir", str(repo_dir),
                    "--hub-dir", str(HUB_DIR),
                    "--skip-remote",
                    "--now", "2026-11-24T13:00:00+08:00",
                ]
            )
            self.assertEqual(rc2, 0)
            self.assertEqual(claude_md.read_text(encoding="utf-8").count(yao_sync.BEGIN_MARKER), 1)

            # 2) Project-dir init injects ONLY into project AGENTS.md & CLAUDE.md
            rc3 = yao_sync.main(
                [
                    "init",
                    "--project-dir", str(proj_dir),
                    "--repo-dir", str(repo_dir),
                    "--hub-dir", str(HUB_DIR),
                    "--skip-remote",
                ]
            )
            self.assertEqual(rc3, 0)
            self.assertTrue((proj_dir / "AGENTS.md").is_file())
            self.assertTrue((proj_dir / "CLAUDE.md").is_file())

            # 3) Non-existent --project-dir fails cleanly (rc=2) without creating directory
            rc_bad_proj = yao_sync.main(
                [
                    "init",
                    "--project-dir", str(self.tmp / "nonexistent_dir"),
                    "--repo-dir", str(repo_dir),
                    "--hub-dir", str(HUB_DIR),
                    "--skip-remote",
                ]
            )
            self.assertEqual(rc_bad_proj, 2)
            self.assertFalse((self.tmp / "nonexistent_dir").exists())
        finally:
            if prev_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = prev_home

    def test_dynamic_schedule_preflight_and_push_gates_and_offline_queue(self) -> None:
        repo_dir = self.tmp / "repo"
        yao_sync.main(
            [
                "init",
                "--no-agent-md",
                "--repo-dir", str(repo_dir),
                "--hub-dir", str(HUB_DIR),
                "--skip-remote",
            ]
        )

        # In bootstrap_week window (2026-11-24 13:00) without report -> WEEKLY_REPORT_DUE
        buf = io.StringIO()
        with redirect_stdout(buf):
            yao_sync.main(
                [
                    "preflight",
                    "--repo-dir", str(repo_dir),
                    "--hub-dir", str(HUB_DIR),
                    "--skip-remote",
                    "--json",
                    "--now", "2026-11-24T13:00:00+08:00",
                ]
            )
        pf1 = json.loads(buf.getvalue())
        self.assertEqual(pf1["action"], "WEEKLY_REPORT_DUE")
        self.assertEqual(pf1["active_week"], "2026-W48")
        self.assertFalse(pf1["require_human_judgment"])

        # Outside window (2026-11-26 10:00, Thursday morning) -> NONE
        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            yao_sync.main(
                [
                    "preflight",
                    "--repo-dir", str(repo_dir),
                    "--hub-dir", str(HUB_DIR),
                    "--skip-remote",
                    "--json",
                    "--now", "2026-11-26T10:00:00+08:00",
                ]
            )
        pf2 = json.loads(buf2.getvalue())
        self.assertEqual(pf2["action"], "NONE")

        # Regular Monday window (2026-11-30 12:00 is Monday before 18:00 deadline, week 2026-W49)
        buf3 = io.StringIO()
        with redirect_stdout(buf3):
            yao_sync.main(
                [
                    "preflight",
                    "--repo-dir", str(repo_dir),
                    "--hub-dir", str(HUB_DIR),
                    "--skip-remote",
                    "--json",
                    "--now", "2026-11-30T12:00:00+08:00",
                ]
            )
        pf3 = json.loads(buf3.getvalue())
        self.assertEqual(pf3["action"], "WEEKLY_REPORT_DUE")
        self.assertEqual(pf3["active_week"], "2026-W49")

        # Push bad weekly report (missing 失败) should BLOCK (exit 4)
        w48 = repo_dir / "weekly" / "2026-W48"
        w48.mkdir(parents=True, exist_ok=True)
        (w48 / "report.md").write_text("# 本周周报\n\n本周工作顺利推进，效果很好，完成若干模块重构与代码优化任务。" * 3, encoding="utf-8")
        rc_bad = yao_sync.main(
            [
                "push",
                "--repo-dir", str(repo_dir),
                "--hub-dir", str(HUB_DIR),
                "--mode", "weekly",
                "--week", "2026-W48",
                "--skip-remote",
            ]
        )
        self.assertEqual(rc_bad, 4)

        # Push good v1.1 AI MVP report.md should PASS and auto-create meta.yaml + commit
        shutil.copy2(HUB_DIR / "examples" / "good-weekly-example" / "report.md", w48 / "report.md")
        rc_good = yao_sync.main(
            [
                "push",
                "--repo-dir", str(repo_dir),
                "--hub-dir", str(HUB_DIR),
                "--mode", "weekly",
                "--week", "2026-W48",
                "--skip-remote",
            ]
        )
        self.assertEqual(rc_good, 0)
        self.assertTrue((w48 / "meta.yaml").is_file())

        # Re-run preflight inside 2026-W48 window -> now action is NONE and git status stays clean!
        buf4 = io.StringIO()
        with redirect_stdout(buf4):
            yao_sync.main(
                [
                    "preflight",
                    "--repo-dir", str(repo_dir),
                    "--hub-dir", str(HUB_DIR),
                    "--skip-remote",
                    "--json",
                    "--now", "2026-11-24T13:00:00+08:00",
                ]
            )
        pf4 = json.loads(buf4.getvalue())
        self.assertEqual(pf4["action"], "NONE")
        self.assertEqual(pf4["submission_state"], "submitted_ai_mvp")
        st_after_preflight = subprocess.run(
            ["git", "-C", str(repo_dir), "status", "--porcelain"],
            text=True, capture_output=True, check=True,
        ).stdout.strip()
        self.assertEqual(st_after_preflight, "")

        # Secret scan gate blocks leaked token
        fake_tok = "ghp_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
        (repo_dir / "logs" / "2026-11-24.md").write_text(f"token: {fake_tok}\n", encoding="utf-8")
        rc_sec = yao_sync.main(
            [
                "push",
                "--repo-dir", str(repo_dir),
                "--hub-dir", str(HUB_DIR),
                "--mode", "daily",
                "--skip-remote",
            ]
        )
        self.assertEqual(rc_sec, 3)
        (repo_dir / "logs" / "2026-11-24.md").write_text("# 2026-11-24\n- 完成测试验证。\n", encoding="utf-8")

        # Offline resilience: broken local remote still commits cleanly and returns 0
        subprocess.run(
            ["git", "-C", str(repo_dir), "remote", "add", "origin", str(self.tmp / "nonexistent-remote.git")],
            check=True,
        )
        rc_offline = yao_sync.main(
            [
                "push",
                "--repo-dir", str(repo_dir),
                "--hub-dir", str(HUB_DIR),
                "--mode", "daily",
            ]
        )
        self.assertEqual(rc_offline, 0)

    def test_secretary_collect_hybrid_v10_and_v11_and_xss_escape(self) -> None:
        # Student 1: v1.1 AI MVP report.md without manual Chinese name, plus XSS probe
        s1 = self.tmp / "s1"
        yao_sync.main(
            [
                "init",
                "--no-agent-md",
                "--repo-dir", str(s1),
                "--hub-dir", str(HUB_DIR),
                "--skip-remote",
            ]
        )
        (s1 / "logs" / "2026-11-24.md").write_text("- [12:00 | proj | main] 完成基准评测。\n", encoding="utf-8")
        w1 = s1 / "weekly" / "2026-W48"
        w1.mkdir(parents=True, exist_ok=True)
        rep_txt = (HUB_DIR / "examples" / "good-weekly-example" / "report.md").read_text(encoding="utf-8")
        (w1 / "report.md").write_text(rep_txt + "\n<script>alert('xss')</script>\n", encoding="utf-8")
        yao_sync.main(
            [
                "push",
                "--repo-dir", str(s1),
                "--hub-dir", str(HUB_DIR),
                "--mode", "weekly",
                "--week", "2026-W48",
                "--skip-remote",
            ]
        )

        # Student 2: v1.0 legacy human-verified evidence.md + judgment.md
        s2 = self.tmp / "s2"
        yao_sync.main(
            [
                "init",
                "--no-agent-md",
                "--repo-dir", str(s2),
                "--hub-dir", str(HUB_DIR),
                "--skip-remote",
            ]
        )
        w2 = s2 / "weekly" / "2026-W48"
        w2.mkdir(parents=True, exist_ok=True)
        for fname in ("evidence.md", "judgment.md", "meta.yaml"):
            shutil.copy2(HUB_DIR / "examples" / "good-weekly-example" / fname, w2 / fname)
        (w2 / "meta.yaml").write_text(
            (w2 / "meta.yaml").read_text(encoding="utf-8").replace("2026-W47", "2026-W48").replace("张明（示例）", "学生乙"),
            encoding="utf-8",
        )
        subprocess.run(["git", "-C", str(s2), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(s2), "commit", "-m", "init s2"], check=True, capture_output=True)

        ws = self.tmp / "sec_ws"
        rc = secretary_collect.main(
            [
                "--workspace", str(ws),
                "--add-repo", f"alice/yao-research-memory={s1}",
                "--add-repo", f"bob/yao-research-bob={s2}",
                "--sync",
                "--digest",
                "--week", "2026-W48",
                "--hub-dir", str(HUB_DIR),
                "--skip-remote",
            ]
        )
        self.assertEqual(rc, 0)

        out = ws / "digests" / "2026-W48"
        status = json.loads((out / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["summary"]["total"], 2)
        self.assertEqual(status["summary"]["submitted_total"], 2)
        self.assertEqual(status["summary"]["submitted_ai_mvp"], 1)
        self.assertEqual(status["summary"]["submitted_human"], 1)

        html_text = (out / "advisor-report.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>alert('xss')</script>", html_text)
        self.assertIn("&lt;script&gt;alert", html_text)
        self.assertIn("学生乙", html_text)
        self.assertIn("已提交·AI生成MVP", html_text)
        self.assertIn("已齐备·本人确认", html_text)


if __name__ == "__main__":
    unittest.main()
