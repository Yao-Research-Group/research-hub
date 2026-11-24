"""Offline unit tests for Yao Research Hub v2.0 (privacy-first manual progress sync)."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
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


class HubV20PrivacyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="yao-hub-v20-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_init_requires_project_dir_and_never_touches_any_agent_rules_or_telemetry(self) -> None:
        fake_home = self.tmp / "home"
        (fake_home / ".claude").mkdir(parents=True)
        (fake_home / ".codex").mkdir(parents=True)
        claude_md = fake_home / ".claude" / "CLAUDE.md"
        claude_md.write_text("# Original Global Rules\n", encoding="utf-8")

        proj_dir = self.tmp / "research_proj"
        proj_dir.mkdir(parents=True)
        repo_dir = self.tmp / "repo"
        policy_file = self.tmp / "local-policy.json"

        prev_home = os.environ.get("HOME")
        os.environ["HOME"] = str(fake_home)
        try:
            # 1) Missing --project-dir MUST fail (rc=2)
            rc_no_proj = yao_sync.main(
                [
                    "init",
                    "--repo-dir", str(repo_dir),
                    "--hub-dir", str(HUB_DIR),
                    "--policy-file", str(policy_file),
                    "--skip-remote",
                ]
            )
            self.assertEqual(rc_no_proj, 2)

            # 2) Valid --project-dir succeeds, installs hook + local-policy.json, NEVER touches AGENTS/CLAUDE/global
            rc_ok = yao_sync.main(
                [
                    "init",
                    "--project-dir", str(proj_dir),
                    "--repo-dir", str(repo_dir),
                    "--hub-dir", str(HUB_DIR),
                    "--policy-file", str(policy_file),
                    "--skip-remote",
                ]
            )
            self.assertEqual(rc_ok, 0)
            self.assertEqual(claude_md.read_text(encoding="utf-8"), "# Original Global Rules\n")
            self.assertFalse((fake_home / ".codex" / "AGENTS.md").exists())
            self.assertFalse((proj_dir / "AGENTS.md").exists())
            self.assertFalse((proj_dir / "CLAUDE.md").exists())
            self.assertFalse((repo_dir / ".yao-notebook.json").exists())

            self.assertTrue(policy_file.is_file())
            pol = json.loads(policy_file.read_text(encoding="utf-8"))
            self.assertEqual(pol["allowed_project_dirs"], [str(proj_dir.resolve())])
            self.assertTrue((repo_dir / ".git" / "hooks" / "pre-commit").is_file())
            self.assertTrue(os.access(repo_dir / ".git" / "hooks" / "pre-commit", os.X_OK))

            # Verify zero host/email/path telemetry inside repo files
            profile_txt = (repo_dir / "profile.md").read_text(encoding="utf-8")
            self.assertNotIn(str(proj_dir), profile_txt)
            self.assertNotIn("@", profile_txt)
        finally:
            if prev_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = prev_home

    def test_cwd_allowlist_preview_confirm_gate_and_precommit_hook(self) -> None:
        proj_dir = self.tmp / "allowed_proj"
        other_dir = self.tmp / "other_unrelated"
        proj_dir.mkdir(parents=True)
        other_dir.mkdir(parents=True)
        repo_dir = self.tmp / "repo"
        policy_file = self.tmp / "local-policy.json"

        yao_sync.main(
            [
                "init",
                "--project-dir", str(proj_dir),
                "--repo-dir", str(repo_dir),
                "--hub-dir", str(HUB_DIR),
                "--policy-file", str(policy_file),
                "--skip-remote",
            ]
        )
        # Initial commit of clean skeleton
        rc_init_push = yao_sync.main(
            [
                "push",
                "--confirm",
                "--from-dir", str(proj_dir),
                "--repo-dir", str(repo_dir),
                "--hub-dir", str(HUB_DIR),
                "--policy-file", str(policy_file),
                "--skip-remote",
            ]
        )
        self.assertEqual(rc_init_push, 0)

        # 1) Non-allowlisted directory MUST fail (rc=5)
        rc_bad_cwd = yao_sync.main(
            [
                "preview",
                "--from-dir", str(other_dir),
                "--repo-dir", str(repo_dir),
                "--hub-dir", str(HUB_DIR),
                "--policy-file", str(policy_file),
            ]
        )
        self.assertEqual(rc_bad_cwd, 5)

        # 2) Valid progress file passes preview and prints plaintext, blocks push without --confirm (rc=6), passes with --confirm
        prog = repo_dir / "progress" / "2026-11-24.md"
        prog.write_text("# 今日进展\n- 完成对比实验基线评测。\n- 失败：43步蒸馏未收敛。\n", encoding="utf-8")

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc_prev = yao_sync.main(
                [
                    "preview",
                    "--from-dir", str(proj_dir),
                    "--repo-dir", str(repo_dir),
                    "--hub-dir", str(HUB_DIR),
                    "--policy-file", str(policy_file),
                ]
            )
        self.assertEqual(rc_prev, 0)
        self.assertIn("完成对比实验基线评测", buf.getvalue())

        rc_no_confirm = yao_sync.main(
            [
                "push",
                "--from-dir", str(proj_dir),
                "--repo-dir", str(repo_dir),
                "--hub-dir", str(HUB_DIR),
                "--policy-file", str(policy_file),
                "--skip-remote",
            ]
        )
        self.assertEqual(rc_no_confirm, 6)

        rc_confirm = yao_sync.main(
            [
                "push",
                "--confirm",
                "--from-dir", str(proj_dir),
                "--repo-dir", str(repo_dir),
                "--hub-dir", str(HUB_DIR),
                "--policy-file", str(policy_file),
                "--skip-remote",
            ]
        )
        self.assertEqual(rc_confirm, 0)

        # 3) Test native .git/hooks/pre-commit directly via raw `git commit` against 6 violation types
        bad_cases = [
            ("src/leak.py", "print('source code')\n"),
            ("progress/2026-11-25.md", "查看 /Users/alice/OneDrive/secret.txt\n"),
            ("progress/2026-11-25.md", "目录在 ~/Desktop/private 下面\n"),
            ("progress/2026-11-25.md", "token=" + "ghp_" + "A" * 28 + "\n"),
            ("progress/2026-11-25.md", "联系邮箱 alice@example.com 或 13812345678\n"),
            ("progress/2026-11-25.md", "a" * 70000 + "\n"),
        ]
        for rel, content in bad_cases:
            p = repo_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            subprocess.run(["git", "-C", str(repo_dir), "add", "-f", rel], check=True)
            res = subprocess.run(
                ["git", "-C", str(repo_dir), "commit", "-m", "should fail"],
                text=True, capture_output=True,
            )
            self.assertNotEqual(res.returncode, 0, f"pre-commit hook failed to block {rel}: {content[:40]}")
            subprocess.run(["git", "-C", str(repo_dir), "reset", "HEAD", "--", rel], check=True, capture_output=True)
            p.unlink(missing_ok=True)
        shutil.rmtree(repo_dir / "src", ignore_errors=True)

        # 4) Legacy untouched files (e.g. old daily/) do NOT block committing valid new progress/
        legacy_f = repo_dir / "daily" / "old.md"
        legacy_f.parent.mkdir(parents=True, exist_ok=True)
        legacy_f.write_text("legacy note\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo_dir), "add", "daily/old.md"], check=True)
        subprocess.run(["git", "-C", str(repo_dir), "commit", "--no-verify", "-m", "legacy"], check=True, capture_output=True)

        (repo_dir / "progress" / "2026-11-26.md").write_text("# 今日进展\n- 推进消融实验。\n", encoding="utf-8")
        rc_after_legacy = yao_sync.main(
            [
                "push",
                "--confirm",
                "--from-dir", str(proj_dir),
                "--repo-dir", str(repo_dir),
                "--hub-dir", str(HUB_DIR),
                "--policy-file", str(policy_file),
                "--skip-remote",
            ]
        )
        self.assertEqual(rc_after_legacy, 0)

    def test_secretary_collect_v2_progress_and_weekly_xss_escape(self) -> None:
        # Student 1: only progress/2026-11-24.md (plus XSS probe)
        s1 = self.tmp / "s1"
        p1 = self.tmp / "p1"
        p1.mkdir(parents=True)
        pol1 = self.tmp / "pol1.json"
        yao_sync.main(
            [
                "init",
                "--project-dir", str(p1),
                "--repo-dir", str(s1),
                "--hub-dir", str(HUB_DIR),
                "--policy-file", str(pol1),
                "--skip-remote",
            ]
        )
        (s1 / "progress" / "2026-11-24.md").write_text(
            "# 今日进展\n- 完成测试基线。\n<script>alert('xss')</script>\n", encoding="utf-8"
        )
        rc_p1 = yao_sync.main(
            [
                "push",
                "--confirm",
                "--from-dir", str(p1),
                "--repo-dir", str(s1),
                "--hub-dir", str(HUB_DIR),
                "--policy-file", str(pol1),
                "--skip-remote",
            ]
        )
        self.assertEqual(rc_p1, 0)

        # Student 2: v2.0 weekly report.md
        s2 = self.tmp / "s2"
        p2 = self.tmp / "p2"
        p2.mkdir(parents=True)
        pol2 = self.tmp / "pol2.json"
        yao_sync.main(
            [
                "init",
                "--project-dir", str(p2),
                "--repo-dir", str(s2),
                "--hub-dir", str(HUB_DIR),
                "--policy-file", str(pol2),
                "--skip-remote",
            ]
        )
        w2 = s2 / "weekly" / "2026-W48"
        w2.mkdir(parents=True, exist_ok=True)
        shutil.copy2(HUB_DIR / "examples" / "good-weekly-example" / "report.md", w2 / "report.md")
        rc_p2 = yao_sync.main(
            [
                "push",
                "--confirm",
                "--from-dir", str(p2),
                "--repo-dir", str(s2),
                "--hub-dir", str(HUB_DIR),
                "--policy-file", str(pol2),
                "--mode", "weekly",
                "--week", "2026-W48",
                "--skip-remote",
            ]
        )
        self.assertEqual(rc_p2, 0)

        ws = self.tmp / "sec_ws"
        rc = secretary_collect.main(
            [
                "--workspace", str(ws),
                "--add-repo", f"alice/yao-research-memory={s1}",
                "--add-repo", f"bob/yao-research-memory={s2}",
                "--sync",
                "--digest",
                "--week", "2026-W48",
                "--hub-dir", str(HUB_DIR),
                "--skip-remote",
            ]
        )
        self.assertEqual(rc, 0)

        out = ws / "digests" / "2026-W48"
        st = json.loads((out / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(st["summary"]["total"], 2)
        self.assertEqual(st["summary"]["progress_only"], 1)
        self.assertEqual(st["summary"]["submitted_ai_mvp"], 1)

        html_text = (out / "advisor-report.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>alert('xss')</script>", html_text)
        self.assertIn("&lt;script&gt;alert", html_text)
        self.assertIn("已交今日进展（周报待交）", html_text)
        self.assertIn("已提交周报", html_text)

    def test_collaborator_422_fallback_and_filter(self) -> None:
        calls = []

        def fake_run_cmd(cmd, cwd=None, check=True, timeout=None):
            calls.append(list(cmd))
            if cmd[:3] == ["gh", "api", "repos/alice/yao-research-memory"]:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=json.dumps({"private": True}), stderr=""
                )
            if (
                cmd[:4] == ["gh", "api", "-X", "PUT"]
                and "collaborators/taoyao2005" in cmd[4]
                and "-f" in cmd
            ):
                return subprocess.CompletedProcess(
                    cmd,
                    1,
                    stdout="",
                    stderr="gh: Validation Failed (HTTP 422): Cannot assign taoyao2005 permission of read",
                )
            if (
                cmd[:4] == ["gh", "api", "-X", "PUT"]
                and "collaborators/taoyao2005" in cmd[4]
                and "-f" not in cmd
            ):
                return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")
            raise AssertionError(f"Unexpected cmd: {cmd}")

        orig_run_cmd = yao_sync.run_cmd
        yao_sync.run_cmd = fake_run_cmd
        try:
            # 1) Owner skipped; advisor hits HTTP 422 on permission=pull -> retries without -f permission
            collabs = [
                {"github": "alice", "role": "owner", "permission": "pull"},
                {"github": "taoyao2005", "role": "advisor", "permission": "pull"},
            ]
            out_buf = io.StringIO()
            with redirect_stdout(out_buf):
                full = yao_sync.ensure_private_remote_repo(
                    owner="alice",
                    repo_name="yao-research-memory",
                    collaborators=collabs,
                    existing_origin_slug=None,
                    dry_run=False,
                )
            self.assertEqual(full, "alice/yao-research-memory")
            self.assertEqual(
                calls,
                [
                    ["gh", "api", "repos/alice/yao-research-memory"],
                    ["gh", "api", "-X", "PUT", "repos/alice/yao-research-memory/collaborators/taoyao2005", "-f", "permission=pull"],
                    ["gh", "api", "-X", "PUT", "repos/alice/yao-research-memory/collaborators/taoyao2005"],
                ],
            )
            self.assertIn("标准协作者邀请", out_buf.getvalue())
        finally:
            yao_sync.run_cmd = orig_run_cmd

        # 2) --collaborators filter via resolve_collaborators / init --collaborators
        selected = yao_sync.resolve_collaborators(
            [
                {"github": "taoyao2005", "role": "advisor", "permission": "pull"},
                {"github": "treerobin06", "role": "secretary", "permission": "pull"},
            ],
            "taoyao2005",
        )
        self.assertEqual(selected, [{"github": "taoyao2005", "role": "advisor", "permission": "pull"}])
        custom = yao_sync.resolve_collaborators(
            [{"github": "taoyao2005", "role": "advisor", "permission": "pull"}],
            "taoyao2005,bob",
        )
        self.assertEqual(
            custom,
            [
                {"github": "taoyao2005", "role": "advisor", "permission": "pull"},
                {"github": "bob", "role": "collaborator", "permission": "pull"},
            ],
        )

        # 3) CLI --collaborators option parses cleanly and passes through init --dry-run
        p_cli = self.tmp / "cli_proj"
        p_cli.mkdir(parents=True)
        parsed = yao_sync.parse_args(
            ["init", "--project-dir", str(p_cli), "--collaborators", "taoyao2005", "--skip-remote", "--dry-run"]
        )
        self.assertEqual(parsed.collaborators, "taoyao2005")
        rc_cli = yao_sync.main(
            [
                "init",
                "--project-dir", str(p_cli),
                "--repo-dir", str(self.tmp / "cli_repo"),
                "--hub-dir", str(HUB_DIR),
                "--policy-file", str(self.tmp / "cli_pol.json"),
                "--collaborators", "taoyao2005",
                "--skip-remote",
                "--dry-run",
            ]
        )
        self.assertEqual(rc_cli, 0)


if __name__ == "__main__":
    unittest.main()
