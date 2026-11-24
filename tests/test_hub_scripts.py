"""Offline unit tests for Yao Research Hub v3.0."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HUB_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = HUB_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import advisor_collect  # noqa: E402
import secretary_collect  # noqa: E402
import yao_sync  # noqa: E402


class HubV30Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="yao-hub-v30-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_init_default_collaborator_is_advisor_only_and_zero_global_touch(self) -> None:
        version_cfg = json.loads((HUB_DIR / "protocol" / "version.json").read_text(encoding="utf-8"))
        collabs = [c["github"] for c in version_cfg["required_collaborators"]]
        self.assertEqual(collabs, ["taoyao2005"])

        fake_home = self.tmp / "home"
        (fake_home / ".claude").mkdir(parents=True)
        (fake_home / ".codex").mkdir(parents=True)
        claude_global = fake_home / ".claude" / "CLAUDE.md"
        claude_global.write_text("# Original Global Rules\n", encoding="utf-8")

        proj_dir = self.tmp / "research_proj"
        proj_dir.mkdir(parents=True)
        repo_dir = self.tmp / "repo"
        policy_file = self.tmp / "local-policy.json"

        prev_home = os.environ.get("HOME")
        os.environ["HOME"] = str(fake_home)
        try:
            # 1) Missing --project-dir MUST fail
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

            # 2) --mode manual (default): touches neither global nor project rules
            rc_manual = yao_sync.main(
                [
                    "init",
                    "--project-dir", str(proj_dir),
                    "--repo-dir", str(repo_dir),
                    "--hub-dir", str(HUB_DIR),
                    "--policy-file", str(policy_file),
                    "--skip-remote",
                ]
            )
            self.assertEqual(rc_manual, 0)
            self.assertEqual(claude_global.read_text(encoding="utf-8"), "# Original Global Rules\n")
            self.assertFalse((fake_home / ".codex" / "AGENTS.md").exists())
            self.assertFalse((proj_dir / "AGENTS.md").exists())
            self.assertFalse((proj_dir / "CLAUDE.md").exists())
            self.assertTrue(policy_file.is_file())
            mode_bits = stat.S_IMODE(policy_file.stat().st_mode)
            self.assertEqual(mode_bits, 0o600)

            pol = json.loads(policy_file.read_text(encoding="utf-8"))
            self.assertEqual(pol["allowed_project_dirs"], [str(proj_dir.resolve())])
            self.assertEqual(pol["mode"], "manual")
            self.assertEqual(pol["confirm_mode"], "confirm")
            self.assertTrue((repo_dir / ".git" / "hooks" / "pre-commit").is_file())
            self.assertTrue(os.access(repo_dir / ".git" / "hooks" / "pre-commit", os.X_OK))

            profile_txt = (repo_dir / "profile.md").read_text(encoding="utf-8")
            self.assertNotIn(str(proj_dir), profile_txt)
            self.assertNotIn("@", profile_txt)

            # 3) --mode project-rule writes ONLY to <project_dir>/AGENTS.md idempotently, never $HOME
            proj_agents = proj_dir / "AGENTS.md"
            proj_agents.write_text("# Existing Project Rules\n", encoding="utf-8")
            for _ in range(2):
                rc_pr = yao_sync.main(
                    [
                        "init",
                        "--project-dir", str(proj_dir),
                        "--repo-dir", str(repo_dir),
                        "--hub-dir", str(HUB_DIR),
                        "--policy-file", str(policy_file),
                        "--mode", "project-rule",
                        "--confirm-mode", "auto-in-allowlist",
                        "--skip-remote",
                    ]
                )
                self.assertEqual(rc_pr, 0)

            agents_txt = proj_agents.read_text(encoding="utf-8")
            self.assertEqual(agents_txt.count(yao_sync.MARKER_START), 1)
            self.assertEqual(agents_txt.count(yao_sync.MARKER_END), 1)
            self.assertIn("# Existing Project Rules", agents_txt)
            self.assertIn("judgment.md", agents_txt)
            self.assertEqual(claude_global.read_text(encoding="utf-8"), "# Original Global Rules\n")
            self.assertFalse((fake_home / ".codex" / "AGENTS.md").exists())
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

        # 1) Non-allowlisted directory fails (rc=5)
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

        # 2) Valid progress file passes preview, blocks push without --confirm (rc=6), passes with --confirm
        prog = repo_dir / "progress" / "2026-11-25.md"
        prog.write_text("# 今日进展\n- 完成端到端基线评测。\n- 失败：43步蒸馏未收敛。\n", encoding="utf-8")

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
        self.assertIn("完成端到端基线评测", buf.getvalue())

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

        # 3) Raw git commit pre-commit blocks disallowed paths, weights, .env, secrets, >10MiB, symlinks, unknown binary
        outside = self.tmp / "secret_outside.txt"
        outside.write_text("outside", encoding="utf-8")
        bad_cases = [
            ("random_dir/leak.txt", b"not in repo allowlist\n"),
            ("code/.env", b"API_KEY=123456\n"),
            ("code/weights/model.pt", b"fake weights"),
            ("code/leak.py", ("token = '" + "ghp_" + "A" * 28 + "'\n").encode("utf-8")),
            ("code/unknown.bin", b"\x00\x01\x02\x03binary"),
            ("code/too_big.txt", b"a" * (10 * 1024 * 1024 + 128)),
        ]
        for rel, content in bad_cases:
            p = repo_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
            subprocess.run(["git", "-C", str(repo_dir), "add", "-f", rel], check=True)
            res = subprocess.run(
                ["git", "-C", str(repo_dir), "commit", "-m", "should fail"],
                text=True, capture_output=True,
            )
            self.assertNotEqual(res.returncode, 0, f"pre-commit failed to block {rel}")
            subprocess.run(["git", "-C", str(repo_dir), "reset", "HEAD", "--", rel], check=True, capture_output=True)
            p.unlink(missing_ok=True)
        shutil.rmtree(repo_dir / "random_dir", ignore_errors=True)

        # Symlink check in pre-commit
        sym_p = repo_dir / "code" / "bad_link.py"
        os.symlink(outside, sym_p)
        subprocess.run(["git", "-C", str(repo_dir), "add", "-f", "code/bad_link.py"], check=True)
        res_sym = subprocess.run(["git", "-C", str(repo_dir), "commit", "-m", "symlink"], text=True, capture_output=True)
        self.assertNotEqual(res_sym.returncode, 0)
        subprocess.run(["git", "-C", str(repo_dir), "reset", "HEAD", "--", "code/bad_link.py"], check=True, capture_output=True)
        sym_p.unlink(missing_ok=True)

        # 4) Legacy untouched files (daily/) do NOT block new commits
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

    def test_attach_and_sync_code_directory_jail_and_assets(self) -> None:
        proj_dir = self.tmp / "pfn_proj"
        proj_dir.mkdir(parents=True)
        outside_dir = self.tmp / "outside_secret"
        outside_dir.mkdir(parents=True)
        outside_file = outside_dir / "private.txt"
        outside_file.write_text("outside secret\n", encoding="utf-8")

        # Initialize proj_dir as a tiny git repo with .gitignore
        subprocess.run(["git", "-C", str(proj_dir), "init", "-b", "main"], check=True, capture_output=True)
        (proj_dir / ".gitignore").write_text("ignored_out/\n", encoding="utf-8")
        (proj_dir / "ignored_out").mkdir()
        (proj_dir / "ignored_out" / "temp.log").write_text("skip via gitignore\n", encoding="utf-8")

        (proj_dir / "train.py").write_text("def train():\n    return 42\n", encoding="utf-8")
        (proj_dir / ".env").write_text("SECRET=abc\n", encoding="utf-8")
        (proj_dir / "ckpt.pt").write_bytes(b"weights")
        (proj_dir / "leak.py").write_text("k = '" + "ghp_" + "Z" * 28 + "'\n", encoding="utf-8")
        os.symlink(outside_file, proj_dir / "link_to_outside.py")

        fig_dir = proj_dir / "results"
        fig_dir.mkdir()
        png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
        (fig_dir / "plot.png").write_bytes(png_bytes)
        (fig_dir / "report.pdf").write_bytes(b"%PDF-1.4\n%fake\n")
        (fig_dir / "metrics.csv").write_text("env,score\nCartPole,263.9\n", encoding="utf-8")

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

        # 1) Directory jail blocks attaching outside file, ../ traversal, and symlink outside
        self.assertEqual(
            yao_sync.main(
                [
                    "attach",
                    "--from-dir", str(proj_dir),
                    "--repo-dir", str(repo_dir),
                    "--hub-dir", str(HUB_DIR),
                    "--policy-file", str(policy_file),
                    "--week", "2026-W49",
                    str(outside_file),
                ]
            ),
            5,
        )
        self.assertEqual(
            yao_sync.main(
                [
                    "attach",
                    "--from-dir", str(proj_dir),
                    "--repo-dir", str(repo_dir),
                    "--hub-dir", str(HUB_DIR),
                    "--policy-file", str(policy_file),
                    "--week", "2026-W49",
                    "../outside_secret/private.txt",
                ]
            ),
            5,
        )
        self.assertEqual(
            yao_sync.main(
                [
                    "attach",
                    "--from-dir", str(proj_dir),
                    "--repo-dir", str(repo_dir),
                    "--hub-dir", str(HUB_DIR),
                    "--policy-file", str(policy_file),
                    "--week", "2026-W49",
                    "link_to_outside.py",
                ]
            ),
            5,
        )

        # 2) Valid attach (.png, .pdf, .csv) + sync-code + session note push cleanly
        rc_att = yao_sync.main(
            [
                "attach",
                "--from-dir", str(proj_dir),
                "--repo-dir", str(repo_dir),
                "--hub-dir", str(HUB_DIR),
                "--policy-file", str(policy_file),
                "--week", "2026-W49",
                "results/plot.png",
                "results/report.pdf",
                "results/metrics.csv",
            ]
        )
        self.assertEqual(rc_att, 0)

        rc_sc = yao_sync.main(
            [
                "sync-code",
                "--from-dir", str(proj_dir),
                "--repo-dir", str(repo_dir),
                "--hub-dir", str(HUB_DIR),
                "--policy-file", str(policy_file),
            ]
        )
        self.assertEqual(rc_sc, 0)
        self.assertTrue((repo_dir / "code" / "pfn_proj" / "train.py").is_file())
        self.assertFalse((repo_dir / "code" / "pfn_proj" / ".env").exists())
        self.assertFalse((repo_dir / "code" / "pfn_proj" / "ckpt.pt").exists())
        self.assertFalse((repo_dir / "code" / "pfn_proj" / "leak.py").exists())
        self.assertFalse((repo_dir / "code" / "pfn_proj" / "link_to_outside.py").exists())
        self.assertFalse((repo_dir / "code" / "pfn_proj" / "ignored_out" / "temp.log").exists())

        sess_f = repo_dir / "sessions" / "2026-11-25-alignment.md"
        sess_f.write_text("# 对齐纪要\n- 统一使用 `code/pfn_proj/train.py` 与 `metrics.csv` 口径。\n", encoding="utf-8")

        wdir = repo_dir / "weekly" / "2026-W49"
        (wdir / "evidence.md").write_text(
            "<!-- GENERATED_BY: AI -->\n# 本周证据页\n- 基线见 [metrics.csv](artifacts/metrics.csv) 与 [plot.png](artifacts/plot.png)。\n"
            "- 失败尝试：CPU 43 步蒸馏未收敛，CartPole 仅 21.67 步，探针 0.0%。\n",
            encoding="utf-8",
        )
        (wdir / "judgment.md").write_text(
            "<!-- AUTHORED_BY: HUMAN -->\n# 本周判断页\n"
            "1. 核心结论：暂不上卡放量，先修排序目标并核对 [metrics.csv](artifacts/metrics.csv)。\n"
            "2. 认知变化：推翻离线准确率等价端到端回报的假设。\n",
            encoding="utf-8",
        )

        rc_push = yao_sync.main(
            [
                "push",
                "--confirm",
                "--mode", "weekly",
                "--week", "2026-W49",
                "--from-dir", str(proj_dir),
                "--repo-dir", str(repo_dir),
                "--hub-dir", str(HUB_DIR),
                "--policy-file", str(policy_file),
                "--skip-remote",
            ]
        )
        self.assertEqual(rc_push, 0)
        self.assertTrue((wdir / "artifacts" / "plot.png").is_file())
        self.assertTrue((wdir / "artifacts" / "report.pdf").is_file())

    def test_tier2_warnings_local_path_and_ai_draft_leak(self) -> None:
        proj_dir = self.tmp / "proj_warn"
        proj_dir.mkdir(parents=True)
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
        wdir = repo_dir / "weekly" / "2026-W49"
        wdir.mkdir(parents=True, exist_ok=True)
        (wdir / "evidence.md").write_text(
            "<!-- GENERATED_BY: AI -->\n# 本周证据页\n"
            "- 结果路径：/Users/alice/exp/results.csv\n"
            "- 失败尝试：暂无负结果。\n"
            "- 将本 Evidence Log 作为本地审阅草稿，请确认是否上传。\n",
            encoding="utf-8",
        )

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = yao_sync.main(
                [
                    "preview",
                    "--from-dir", str(proj_dir),
                    "--repo-dir", str(repo_dir),
                    "--hub-dir", str(HUB_DIR),
                    "--policy-file", str(policy_file),
                ]
            )
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("[WARN]", out)
        self.assertIn("死链提醒", out)
        self.assertIn("AI草稿残留提醒", out)

        rc_push = yao_sync.main(
            [
                "push",
                "--confirm",
                "--mode", "weekly",
                "--week", "2026-W49",
                "--from-dir", str(proj_dir),
                "--repo-dir", str(repo_dir),
                "--hub-dir", str(HUB_DIR),
                "--policy-file", str(policy_file),
                "--skip-remote",
            ]
        )
        self.assertEqual(rc_push, 0)

    def test_confirm_mode_auto_in_allowlist_still_guards_judgment(self) -> None:
        proj_dir = self.tmp / "auto_proj"
        proj_dir.mkdir(parents=True)
        repo_dir = self.tmp / "repo"
        policy_file = self.tmp / "local-policy.json"

        yao_sync.main(
            [
                "init",
                "--project-dir", str(proj_dir),
                "--repo-dir", str(repo_dir),
                "--hub-dir", str(HUB_DIR),
                "--policy-file", str(policy_file),
                "--mode", "manual",
                "--confirm-mode", "auto-in-allowlist",
                "--skip-remote",
            ]
        )

        # Progress + code push WITHOUT --confirm succeeds in auto-in-allowlist
        (proj_dir / "eval.py").write_text("print('eval')\n", encoding="utf-8")
        (repo_dir / "progress" / "2026-11-25.md").write_text(
            "# 今日进展\n- 完成 `code/auto_proj/eval.py` 基准评测。\n", encoding="utf-8"
        )
        rc_auto = yao_sync.main(
            [
                "upload",
                "--from-dir", str(proj_dir),
                "--repo-dir", str(repo_dir),
                "--hub-dir", str(HUB_DIR),
                "--policy-file", str(policy_file),
                "--sync-code", ".",
                "--skip-remote",
            ]
        )
        self.assertEqual(rc_auto, 0)

        # Modifying judgment.md MUST still require --confirm (rc=6)
        wdir = repo_dir / "weekly" / "2026-W49"
        wdir.mkdir(parents=True, exist_ok=True)
        (wdir / "evidence.md").write_text(
            "<!-- GENERATED_BY: AI -->\n# 证据页\n- 跑完 `code/auto_proj/eval.py` 基准评测，失败尝试暂无。\n",
            encoding="utf-8",
        )
        (wdir / "judgment.md").write_text(
            "<!-- AUTHORED_BY: HUMAN -->\n# 判断页\n1. 核心结论：本周完成对比并推翻旧假设。\n",
            encoding="utf-8",
        )
        rc_block_jd = yao_sync.main(
            [
                "push",
                "--mode", "weekly",
                "--week", "2026-W49",
                "--from-dir", str(proj_dir),
                "--repo-dir", str(repo_dir),
                "--hub-dir", str(HUB_DIR),
                "--policy-file", str(policy_file),
                "--skip-remote",
            ]
        )
        self.assertEqual(rc_block_jd, 6)

        rc_ok_jd = yao_sync.main(
            [
                "push",
                "--confirm",
                "--mode", "weekly",
                "--week", "2026-W49",
                "--from-dir", str(proj_dir),
                "--repo-dir", str(repo_dir),
                "--hub-dir", str(HUB_DIR),
                "--policy-file", str(policy_file),
                "--skip-remote",
            ]
        )
        self.assertEqual(rc_ok_jd, 0)

    def test_collaborator_422_fallback_and_filter(self) -> None:
        calls = []

        def fake_run_cmd(cmd, cwd=None, check=True, timeout=None, input_bytes=None):
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

        custom = yao_sync.resolve_collaborators(
            [{"github": "taoyao2005", "role": "advisor", "permission": "pull"}],
            "taoyao2005,teammate1",
        )
        self.assertEqual(
            custom,
            [
                {"github": "taoyao2005", "role": "advisor", "permission": "pull"},
                {"github": "teammate1", "role": "collaborator", "permission": "pull"},
            ],
        )

    def test_advisor_collect_and_secretary_compat(self) -> None:
        # Student 1: progress only + XSS probe
        s1, p1, pol1 = self.tmp / "s1", self.tmp / "p1", self.tmp / "pol1.json"
        p1.mkdir()
        yao_sync.main(
            ["init", "--project-dir", str(p1), "--repo-dir", str(s1), "--hub-dir", str(HUB_DIR), "--policy-file", str(pol1), "--skip-remote"]
        )
        (s1 / "progress" / "2026-11-25.md").write_text(
            "# 今日进展\n- 完成基线测试。\n<script>alert('xss')</script>\n", encoding="utf-8"
        )
        self.assertEqual(
            yao_sync.main(["push", "--confirm", "--from-dir", str(p1), "--repo-dir", str(s1), "--hub-dir", str(HUB_DIR), "--policy-file", str(pol1), "--skip-remote"]),
            0,
        )

        # Student 2: dual-track + artifacts + code + sessions
        s2, p2, pol2 = self.tmp / "s2", self.tmp / "p2", self.tmp / "pol2.json"
        p2.mkdir()
        (p2 / "train.py").write_text("print('train')\n", encoding="utf-8")
        (p2 / "metrics.csv").write_text("env,ret\nCartPole,264\n", encoding="utf-8")
        yao_sync.main(
            ["init", "--project-dir", str(p2), "--repo-dir", str(s2), "--hub-dir", str(HUB_DIR), "--policy-file", str(pol2), "--skip-remote"]
        )
        w2 = s2 / "weekly" / "2026-W49"
        w2.mkdir(parents=True, exist_ok=True)
        shutil.copy2(HUB_DIR / "examples" / "good-weekly-example" / "evidence.md", w2 / "evidence.md")
        shutil.copy2(HUB_DIR / "examples" / "good-weekly-example" / "judgment.md", w2 / "judgment.md")
        meta_txt = (HUB_DIR / "examples" / "good-weekly-example" / "meta.yaml").read_text(encoding="utf-8")
        (w2 / "meta.yaml").write_text(meta_txt.replace("2026-W47", "2026-W49"), encoding="utf-8")
        (s2 / "sessions" / "2026-11-25-sync.md").write_text("# 讨论摘要\n- 对齐训练步数。\n", encoding="utf-8")
        self.assertEqual(
            yao_sync.main(
                [
                    "upload",
                    "--from-dir", str(p2),
                    "--repo-dir", str(s2),
                    "--hub-dir", str(HUB_DIR),
                    "--policy-file", str(pol2),
                    "--week", "2026-W49",
                    "--attach", "metrics.csv",
                    "--sync-code", ".",
                    "--mode", "weekly",
                    "--confirm",
                    "--skip-remote",
                ]
            ),
            0,
        )

        # Student 3: missing judgment + local abs path in evidence.md -> audit_flags populated
        s3, p3, pol3 = self.tmp / "s3", self.tmp / "p3", self.tmp / "pol3.json"
        p3.mkdir()
        yao_sync.main(
            ["init", "--project-dir", str(p3), "--repo-dir", str(s3), "--hub-dir", str(HUB_DIR), "--policy-file", str(pol3), "--skip-remote"]
        )
        w3 = s3 / "weekly" / "2026-W49"
        w3.mkdir(parents=True, exist_ok=True)
        (w3 / "evidence.md").write_text(
            "<!-- GENERATED_BY: AI -->\n# 证据页\n"
            "- 结果文件位于 /Users/charlie/exp/run1.json ，本次在 Grid5 跑出 91.2%，对比基线 78.0% 提升。\n"
            "- 失败尝试：CartPole 未收敛。\n",
            encoding="utf-8",
        )
        self.assertEqual(
            yao_sync.main(
                [
                    "push",
                    "--confirm",
                    "--mode", "weekly",
                    "--week", "2026-W49",
                    "--from-dir", str(p3),
                    "--repo-dir", str(s3),
                    "--hub-dir", str(HUB_DIR),
                    "--policy-file", str(pol3),
                    "--skip-remote",
                ]
            ),
            0,
        )

        # Test advisor_collect
        ws = self.tmp / "adv_ws"
        rc_adv = advisor_collect.main(
            [
                "--workspace", str(ws),
                "--add-repo", f"alice/yao-research-memory={s1}",
                "--add-repo", f"bob/yao-research-memory={s2}",
                "--add-repo", f"charlie/yao-research-memory={s3}",
                "--sync",
                "--digest",
                "--week", "2026-W49",
                "--hub-dir", str(HUB_DIR),
                "--skip-remote",
            ]
        )
        self.assertEqual(rc_adv, 0)

        out = ws / "digests" / "2026-W49"
        st = json.loads((out / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(st["summary"]["total"], 3)
        self.assertEqual(st["summary"]["progress_only"], 1)
        self.assertEqual(st["summary"]["submitted_human"], 1)
        self.assertEqual(st["summary"]["missing_judgment"], 1)

        members_by_repo = {m["repo"]: m for m in st["members"]}
        bob = members_by_repo["bob/yao-research-memory"]
        self.assertIn("weekly/2026-W49/artifacts/metrics.csv", bob["artifact_files"])
        self.assertGreaterEqual(bob["code_files_count"], 1)
        self.assertTrue(bob["recent_commits"])
        self.assertTrue(Path(bob["spoke_local_path"]).is_dir())

        charlie = members_by_repo["charlie/yao-research-memory"]
        self.assertIn("missing_judgment", charlie["audit_flags"])
        self.assertIn("has_local_abs_paths", charlie["audit_flags"])

        preread = (out / "advisor-preread-input.md").read_text(encoding="utf-8")
        self.assertIn("code/p2/", preread)
        self.assertIn("artifacts/metrics.csv", preread)
        self.assertIn("has_local_abs_paths", preread)

        html_text = (out / "advisor-report.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>alert('xss')</script>", html_text)
        self.assertIn("&lt;script&gt;alert", html_text)

        # Test secretary_collect compatibility wrapper
        ws_sec = self.tmp / "sec_ws"
        rc_sec = secretary_collect.main(
            [
                "--workspace", str(ws_sec),
                "--add-repo", f"alice/yao-research-memory={s1}",
                "--sync",
                "--digest",
                "--week", "2026-W49",
                "--hub-dir", str(HUB_DIR),
                "--skip-remote",
            ]
        )
        self.assertEqual(rc_sec, 0)
        self.assertTrue((ws_sec / "digests" / "2026-W49" / "advisor-report.html").is_file())


if __name__ == "__main__":
    unittest.main()
