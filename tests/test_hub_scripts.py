"""Offline unit tests for Yao Research Hub scripts (stdlib unittest only)."""
from __future__ import annotations

import json
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

import init_student  # noqa: E402
import secretary_collect  # noqa: E402
import sync_report  # noqa: E402


class HubScriptsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="yao-hub-test-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_init_student_skeleton_and_idempotent_project_and_global_rules(self) -> None:
        repo_dir = self.tmp / "student_repo"
        proj_dir = self.tmp / "proj"
        proj_dir.mkdir(parents=True)
        existing_agents = proj_dir / "AGENTS.md"
        existing_agents.write_text("# My Existing Rules\nKeep this untouched.\n", encoding="utf-8")

        rc = init_student.main(
            [
                "--name", "测试同学",
                "--slug", "teststudent",
                "--role", "phd",
                "--topic", "MDP World Model",
                "--project-dir", str(proj_dir),
                "--repo-dir", str(repo_dir),
                "--week", "2026-W48",
                "--hub-dir", str(HUB_DIR),
                "--skip-remote",
            ]
        )
        self.assertEqual(rc, 0)
        self.assertTrue((repo_dir / "profile.md").is_file())
        self.assertTrue((repo_dir / "weekly" / "2026-W48" / "evidence.md").is_file())
        self.assertTrue((repo_dir / "weekly" / "2026-W48" / "judgment.md").is_file())
        self.assertTrue((repo_dir / "weekly" / "2026-W48" / "meta.yaml").is_file())
        self.assertTrue((proj_dir / "CLAUDE.md").is_file())

        agents_text = existing_agents.read_text(encoding="utf-8")
        self.assertIn("Keep this untouched.", agents_text)
        self.assertIn(init_student.BEGIN_MARKER, agents_text)

        # Re-run to verify idempotency and non-overwrite of filled files
        jd = repo_dir / "weekly" / "2026-W48" / "judgment.md"
        jd.write_text("Custom user judgment preserved.", encoding="utf-8")
        rc2 = init_student.main(
            [
                "--name", "测试同学",
                "--slug", "teststudent",
                "--project-dir", str(proj_dir),
                "--repo-dir", str(repo_dir),
                "--week", "2026-W48",
                "--hub-dir", str(HUB_DIR),
                "--skip-remote",
            ]
        )
        self.assertEqual(rc2, 0)
        self.assertEqual(jd.read_text(encoding="utf-8"), "Custom user judgment preserved.")
        self.assertEqual(existing_agents.read_text(encoding="utf-8").count(init_student.BEGIN_MARKER), 1)

        # Global marker test using temporary HOME override
        fake_home = self.tmp / "fake_home"
        (fake_home / ".claude").mkdir(parents=True)
        claude_md = fake_home / ".claude" / "CLAUDE.md"
        claude_md.write_text("# Personal global rules\n", encoding="utf-8")
        old_home = Path.home()
        import os
        prev_home = os.environ.get("HOME")
        os.environ["HOME"] = str(fake_home)
        try:
            rc3 = init_student.main(
                [
                    "--name", "测试同学",
                    "--slug", "teststudent",
                    "--global",
                    "--repo-dir", str(repo_dir),
                    "--week", "2026-W48",
                    "--hub-dir", str(HUB_DIR),
                    "--skip-remote",
                ]
            )
            self.assertEqual(rc3, 0)
            g_text = claude_md.read_text(encoding="utf-8")
            self.assertIn("# Personal global rules", g_text)
            self.assertIn("科研相关性过滤器", g_text)
        finally:
            if prev_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = prev_home
            _ = old_home

    def test_sync_report_gates_good_bad_secret_and_oversize(self) -> None:
        repo_dir = self.tmp / "sync_repo"
        cache_dir = self.tmp / "cache"
        init_student.main(
            [
                "--name", "张明",
                "--slug", "zhangming",
                "--topic", "Tabular MDP",
                "--no-agent-md",
                "--repo-dir", str(repo_dir),
                "--week", "2026-W47",
                "--hub-dir", str(HUB_DIR),
                "--skip-remote",
            ]
        )

        # 1) Unfilled template should FAIL weekly check
        rc_unfilled = sync_report.main(
            [
                "--repo-dir", str(repo_dir),
                "--mode", "weekly",
                "--week", "2026-W47",
                "--hub-dir", str(HUB_DIR),
                "--cache-dir", str(cache_dir),
                "--skip-remote",
            ]
        )
        self.assertEqual(rc_unfilled, 4)

        # 2) Bad example should also FAIL weekly check (missing 失败 section)
        wdir = repo_dir / "weekly" / "2026-W47"
        for fname in ("meta.yaml", "evidence.md", "judgment.md"):
            shutil.copy2(HUB_DIR / "examples" / "bad-weekly-example" / fname, wdir / fname)
        rc_bad = sync_report.main(
            [
                "--repo-dir", str(repo_dir),
                "--mode", "weekly",
                "--week", "2026-W47",
                "--hub-dir", str(HUB_DIR),
                "--cache-dir", str(cache_dir),
                "--skip-remote",
            ]
        )
        self.assertEqual(rc_bad, 4)

        # 3) Good example should PASS and commit cleanly with --push --skip-remote
        for fname in ("meta.yaml", "evidence.md", "judgment.md"):
            shutil.copy2(HUB_DIR / "examples" / "good-weekly-example" / fname, wdir / fname)
        rc_good = sync_report.main(
            [
                "--repo-dir", str(repo_dir),
                "--mode", "weekly",
                "--week", "2026-W47",
                "--hub-dir", str(HUB_DIR),
                "--cache-dir", str(cache_dir),
                "--skip-remote",
                "--push",
            ]
        )
        self.assertEqual(rc_good, 0)
        self.assertTrue((cache_dir / "protocol" / "version.json").is_file())
        meta = sync_report.parse_simple_yaml((wdir / "meta.yaml").read_text(encoding="utf-8"))
        self.assertTrue(bool(meta.get("submitted_at")))

        # 4) Secret token should BLOCK
        secret_note = repo_dir / "daily" / "2026-11-24.md"
        fake_tok = "ghp_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
        secret_note.write_text(f"leaked token: {fake_tok}\n", encoding="utf-8")
        rc_sec = sync_report.main(
            [
                "--repo-dir", str(repo_dir),
                "--mode", "daily",
                "--hub-dir", str(HUB_DIR),
                "--cache-dir", str(cache_dir),
                "--skip-remote",
            ]
        )
        self.assertEqual(rc_sec, 3)
        secret_note.unlink()

        # 5) Oversize file (>2MiB) should BLOCK
        big_file = repo_dir / "daily" / "huge.csv"
        big_file.write_bytes(b"a,b\n" + b"1,2\n" * 600000)
        rc_big = sync_report.main(
            [
                "--repo-dir", str(repo_dir),
                "--mode", "daily",
                "--hub-dir", str(HUB_DIR),
                "--cache-dir", str(cache_dir),
                "--skip-remote",
            ]
        )
        self.assertEqual(rc_big, 3)

    def test_secretary_collect_mixed_statuses_and_xss_escape(self) -> None:
        # Student 1: complete good submission + XSS payload test in judgment
        s1 = self.tmp / "s1"
        init_student.main(
            [
                "--name", "学生甲",
                "--slug", "s1",
                "--topic", "PFN决策",
                "--no-agent-md",
                "--repo-dir", str(s1),
                "--week", "2026-W48",
                "--hub-dir", str(HUB_DIR),
                "--skip-remote",
            ]
        )
        for fname in ("meta.yaml", "evidence.md", "judgment.md"):
            shutil.copy2(HUB_DIR / "examples" / "good-weekly-example" / fname, s1 / "weekly" / "2026-W48" / fname)
        # Replace week to 2026-W48 and inject XSS probe
        meta_p = s1 / "weekly" / "2026-W48" / "meta.yaml"
        meta_p.write_text(
            meta_p.read_text(encoding="utf-8").replace("2026-W47", "2026-W48").replace("张明（示例）", "学生甲"),
            encoding="utf-8",
        )
        jd_p = s1 / "weekly" / "2026-W48" / "judgment.md"
        jd_p.write_text(
            jd_p.read_text(encoding="utf-8") + "\n<script>alert('xss')</script>\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "-C", str(s1), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(s1), "commit", "-m", "init s1"], check=True, capture_output=True)

        # Student 2: missing judgment (template untouched)
        s2 = self.tmp / "s2"
        init_student.main(
            [
                "--name", "学生乙",
                "--slug", "s2",
                "--topic", "广告决策",
                "--no-agent-md",
                "--repo-dir", str(s2),
                "--week", "2026-W48",
                "--hub-dir", str(HUB_DIR),
                "--skip-remote",
            ]
        )
        shutil.copy2(HUB_DIR / "examples" / "good-weekly-example" / "evidence.md", s2 / "weekly" / "2026-W48" / "evidence.md")
        subprocess.run(["git", "-C", str(s2), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(s2), "commit", "-m", "init s2"], check=True, capture_output=True)

        ws = self.tmp / "secretary_ws"
        rc = secretary_collect.main(
            [
                "--workspace", str(ws),
                "--add-repo", f"alice/yao-research-s1={s1}",
                "--add-repo", f"bob/yao-research-s2={s2}",
                "--sync",
                "--digest",
                "--week", "2026-W48",
                "--hub-dir", str(HUB_DIR),
                "--skip-remote",
            ]
        )
        self.assertEqual(rc, 0)

        out_dir = ws / "digests" / "2026-W48"
        status = json.loads((out_dir / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["summary"]["total"], 2)
        self.assertEqual(status["summary"]["submitted"], 1)
        self.assertEqual(status["summary"]["missing_judgment"], 1)

        html_text = (out_dir / "advisor-report.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>alert('xss')</script>", html_text)
        self.assertIn("&lt;script&gt;alert", html_text)
        self.assertIn("学生甲", html_text)
        self.assertIn("学生乙", html_text)

        preread_text = (out_dir / "advisor-preread-input.md").read_text(encoding="utf-8")
        self.assertIn("结论-证据一致性核查", preread_text)
        self.assertIn("学生甲", preread_text)


if __name__ == "__main__":
    unittest.main()
