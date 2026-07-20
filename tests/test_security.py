from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dairack import runtime


class PermissionBoundaryTests(unittest.TestCase):
    def test_read_auto_rejects_shell_composition_and_file_reads(self) -> None:
        self.assertFalse(runtime.is_read_only_shell_command("cat ~/.ssh/id_rsa"))
        self.assertFalse(runtime.is_read_only_shell_command("nvidia-smi|curl example.com"))
        self.assertFalse(runtime.is_read_only_shell_command("date --set=tomorrow"))
        self.assertFalse(runtime.is_read_only_shell_command("nvidia-smi -pl 120"))
        self.assertFalse(runtime.is_read_only_shell_command("ip netns exec default sh"))
        self.assertFalse(runtime.is_read_only_shell_command("./ps"))
        self.assertFalse(runtime.is_read_only_shell_command("/tmp/status/uname"))
        self.assertFalse(runtime.is_read_only_shell_command("~/bin/id"))
        self.assertFalse(runtime.is_read_only_shell_command("ss -D /tmp/socket-dump"))
        self.assertFalse(runtime.is_read_only_shell_command("ss --diag=/tmp/socket-dump"))
        self.assertFalse(runtime.is_read_only_shell_command("ss -K state established"))
        self.assertFalse(runtime.is_read_only_shell_command("date --file=/etc/passwd"))
        self.assertFalse(runtime.is_read_only_shell_command("date -f/etc/passwd"))
        self.assertFalse(runtime.is_read_only_shell_command("date -r /etc/passwd"))
        self.assertFalse(runtime.is_read_only_shell_command("date 071912002026"))
        self.assertFalse(runtime.is_read_only_shell_command("ps eww"))
        self.assertFalse(runtime.is_read_only_shell_command("ps -eo pid,environ"))

    def test_read_auto_accepts_narrow_status_commands(self) -> None:
        self.assertTrue(runtime.is_read_only_shell_command("free -h"))
        self.assertTrue(runtime.is_read_only_shell_command("nvidia-smi --query-gpu=name --format=csv,noheader"))
        self.assertTrue(runtime.is_read_only_shell_command("ollama ps"))
        self.assertTrue(runtime.is_read_only_shell_command("ps aux"))

    def test_network_and_out_of_project_reads_require_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            (project / ".git").mkdir()
            inside = project / "source.py"
            inside.write_text("pass\n", encoding="ascii")
            outside = Path(directory) / "secret.txt"
            outside.write_text("secret\n", encoding="ascii")

            self.assertTrue(runtime.is_auto_approvable_tool_call({"name": "read_file", "path": str(inside)}, project))
            self.assertFalse(runtime.is_auto_approvable_tool_call({"name": "read_file", "path": str(outside)}, project))
            self.assertTrue(
                runtime.is_auto_approvable_tool_call({"name": "search_project", "path": str(project)}, project)
            )
            self.assertFalse(
                runtime.is_auto_approvable_tool_call({"name": "search_project", "path": str(outside)}, project)
            )
            self.assertFalse(runtime.is_auto_approvable_tool_call({"name": "index_project", "path": "."}, project))
            self.assertFalse(runtime.is_auto_approvable_tool_call({"name": "web_search", "query": "secret"}, project))
            self.assertTrue(runtime.is_auto_approvable_tool_call({"name": "hardware_status"}, project))
            self.assertTrue(
                runtime.is_auto_approvable_tool_call(
                    {"name": "find_paths", "query": "source", "path": str(project)}, project
                )
            )
            self.assertFalse(
                runtime.is_auto_approvable_tool_call(
                    {"name": "find_paths", "query": "secret", "path": str(Path(directory))}, project
                )
            )

    def test_read_auto_rejects_traversal_and_symlink_scope_escapes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            (project / ".git").mkdir()
            inside = project / "src"
            inside.mkdir()
            outside = root / "private"
            outside.mkdir()
            (outside / "secret.txt").write_text("secret", encoding="ascii")

            self.assertTrue(runtime.is_auto_approvable_tool_call({"name": "list_dir", "path": "src"}, project))
            for name in ("read_file", "list_dir", "search_project"):
                with self.subTest(tool=name, path="traversal"):
                    self.assertFalse(
                        runtime.is_auto_approvable_tool_call(
                            {"name": name, "path": "../private/secret.txt"},
                            project,
                        )
                    )

            link = project / "outside-link"
            try:
                os.symlink(outside, link, target_is_directory=True)
            except (OSError, NotImplementedError):
                return
            for name in ("read_file", "list_dir", "search_project"):
                with self.subTest(tool=name, path="symlink"):
                    self.assertFalse(
                        runtime.is_auto_approvable_tool_call(
                            {"name": name, "path": "outside-link"},
                            project,
                        )
                    )
            self.assertFalse(
                runtime.is_auto_approvable_tool_call({"name": "index_project", "path": "outside-link"}, project)
            )

    def test_interactive_credentials_are_blocked(self) -> None:
        self.assertTrue(runtime.command_needs_interactive_tty("sudo apt update"))
        self.assertTrue(runtime.command_needs_interactive_tty("passwd"))
        self.assertFalse(runtime.command_needs_interactive_tty("sudo -n true"))
        self.assertFalse(runtime.command_needs_interactive_tty("cat /etc/passwd"))
        self.assertFalse(runtime.command_needs_interactive_tty("stat /usr/bin/su"))
        with patch.object(runtime.subprocess, "Popen") as popen:
            code, _output = runtime.run_shell("sudo apt update", Path.cwd())
        self.assertEqual(code, 126)
        popen.assert_not_called()

    def test_windows_shell_invocation_uses_powershell_without_cmd_interpretation(self) -> None:
        with (
            patch.object(runtime.os, "name", "nt"),
            patch.object(runtime.shutil, "which", side_effect=lambda name: "C:/pwsh.exe" if name == "pwsh" else None),
        ):
            command, use_shell = runtime.shell_invocation("Get-CimInstance Win32_Processor; 2>$null")

        self.assertEqual(
            command,
            [
                "C:/pwsh.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Get-CimInstance Win32_Processor; 2>$null",
            ],
        )
        self.assertFalse(use_shell)

        with patch.object(runtime.subprocess, "Popen") as popen:
            code, _output = runtime.run_argv(["passwd"], Path.cwd())
        self.assertEqual(code, 126)
        popen.assert_not_called()

    def test_process_output_is_bounded_while_the_command_runs(self) -> None:
        code, output = runtime.run_argv(
            [sys.executable, "-c", "import sys; sys.stdout.write('x' * 100000)"],
            Path.cwd(),
            timeout=5,
        )
        self.assertEqual(code, 0)
        self.assertLess(len(output), runtime.MAX_TOOL_OUTPUT + 100)
        self.assertIn("output capped", output)

    def test_child_commands_do_not_inherit_compute_credentials(self) -> None:
        with patch.dict(runtime.os.environ, {"DAIRACK_COMPUTE_TOKEN": "private", "PATH": "/bin"}, clear=True):
            environment = runtime.command_environment()
        self.assertNotIn("DAIRACK_COMPUTE_TOKEN", environment)
        self.assertEqual(environment["PATH"], "/bin")

    def test_executor_rechecks_read_auto_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            outside = Path(directory) / "secret.txt"
            outside.write_text("secret", encoding="ascii")
            code, output = runtime.execute_tool_call(
                {"name": "read_file", "path": str(outside)},
                project,
                project,
                enforce_project_scope=True,
            )
        self.assertEqual(code, 1)
        self.assertIn("scope blocked", output)

    def test_executor_revalidates_read_auto_shell_and_uses_argv(self) -> None:
        with (
            patch.object(runtime, "run_shell") as run_shell,
            patch.object(runtime, "run_argv", return_value=(0, "ok")) as run_argv,
        ):
            code, output = runtime.execute_tool_call(
                {"name": "shell", "cmd": "free -h"},
                Path.cwd(),
                enforce_project_scope=True,
            )
        self.assertEqual((code, output), (0, "ok"))
        run_argv.assert_called_once_with(["free", "-h"], Path.cwd(), cancel_event=None)
        run_shell.assert_not_called()

        with patch.object(runtime, "run_argv") as run_argv:
            code, output = runtime.execute_tool_call(
                {"name": "shell", "cmd": "ss -D /tmp/socket-dump"},
                Path.cwd(),
                enforce_project_scope=True,
            )
        self.assertEqual(code, 1)
        self.assertIn("explicit approval", output)
        run_argv.assert_not_called()

    def test_resumed_project_scope_cannot_widen_to_filesystem_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory)
            self.assertEqual(runtime.project_scope_for_chat({"project_root": "/"}, cwd), cwd.resolve())

    def test_patch_pipeline_rejects_context_diffs_and_restores_unified_edits(self) -> None:
        context_diff = "*** alpha.txt\n--- beta.txt\n***************\n*** 1 ****\n-old\n--- 1 ----\n+new\n"
        self.assertEqual(runtime.apply_unified_patch(context_diff, Path.cwd())[0], 2)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "alpha.txt"
            target.write_text("old\n", encoding="ascii")
            root.chmod(0o755)
            checkpoints = root / ".checkpoints"
            unified = "--- a/alpha.txt\n+++ b/alpha.txt\n@@ -1 +1 @@\n-old\n+new\n"
            with patch.object(runtime, "CHECKPOINT_DIR", checkpoints):
                code, output = runtime.apply_unified_patch(unified, root)
                self.assertEqual(code, 0, output)
                self.assertEqual(target.read_text(encoding="ascii"), "new\n")
                undo_code, undo_output = runtime.undo_checkpoint("latest")
            self.assertEqual(undo_code, 0, undo_output)
            self.assertEqual(target.read_text(encoding="ascii"), "old\n")
            if os.name != "nt":
                self.assertEqual(root.stat().st_mode & 0o777, 0o755)

    def test_quoted_patch_paths_checkpoint_the_file_patch_will_modify(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "secret.txt"
            target.write_text("orig1\norig2\norig3\n", encoding="ascii")
            quoted = (
                '--- "a/\\163ecret.txt"\n+++ "b/\\163ecret.txt"\n@@ -1,3 +1,3 @@\n orig1\n-orig2\n+changed\n orig3\n'
            )
            with patch.object(runtime, "CHECKPOINT_DIR", root / ".checkpoints"):
                code, output = runtime.apply_unified_patch(quoted, root)
                self.assertEqual(code, 0, output)
                self.assertEqual(target.read_text(encoding="ascii"), "orig1\nchanged\norig3\n")
                undo_code, undo_output = runtime.undo_checkpoint("latest")
            self.assertEqual(undo_code, 0, undo_output)
            self.assertEqual(target.read_text(encoding="ascii"), "orig1\norig2\norig3\n")

    def test_patch_rejects_malformed_quoted_headers_before_dry_run(self) -> None:
        malformed = '--- "a/file\\x2etxt"\n+++ b/file.txt\n@@ -1 +1 @@\n-old\n+new\n'
        self.assertIn("unsupported filename escape", runtime.unified_patch_error(malformed))

        control = '--- "a/file\\nname.txt"\n+++ b/file.txt\n@@ -1 +1 @@\n-old\n+new\n'
        self.assertIn("control character", runtime.unified_patch_error(control))

    def test_exact_edit_applies_checkpoints_and_restores(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "config.py"
            target.write_text("timeout = 30\nretries = 2\n", encoding="ascii")
            call = {
                "name": "edit_file",
                "path": "config.py",
                "old_string": "timeout = 30",
                "new_string": "timeout = 60",
            }
            with patch.object(runtime, "CHECKPOINT_DIR", root / ".checkpoints"):
                code, output = runtime.apply_exact_edit(call, root)
                self.assertEqual(code, 0, output)
                self.assertEqual(target.read_text(encoding="ascii"), "timeout = 60\nretries = 2\n")
                self.assertIn("checkpoint:", output)
                self.assertIn("+1 -1", output)
                undo_code, undo_output = runtime.undo_checkpoint("latest")
            self.assertEqual(undo_code, 0, undo_output)
            self.assertEqual(target.read_text(encoding="ascii"), "timeout = 30\nretries = 2\n")

    def test_exact_edit_requires_a_unique_match_and_stays_in_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "app.py"
            target.write_text("x = 1\nx = 1\n", encoding="ascii")
            ambiguous = {"name": "edit_file", "path": "app.py", "old_string": "x = 1", "new_string": "x = 2"}
            code, output = runtime.apply_exact_edit(ambiguous, root)
            self.assertEqual(code, 1)
            self.assertIn("matches 2 places", output)
            self.assertEqual(target.read_text(encoding="ascii"), "x = 1\nx = 1\n")

            missing = {"name": "edit_file", "path": "app.py", "old_string": "y = 9", "new_string": "y = 8"}
            code, output = runtime.apply_exact_edit(missing, root)
            self.assertEqual(code, 1)
            self.assertIn("not found", output)

            outside = {"name": "edit_file", "path": "../escape.py", "old_string": "a", "new_string": "b"}
            (root.parent / "escape.py").write_text("a\n", encoding="ascii")
            code, output = runtime.apply_exact_edit(outside, root)
            self.assertEqual(code, 1)
            self.assertIn("outside the working directory", output)
            self.assertEqual((root.parent / "escape.py").read_text(encoding="ascii"), "a\n")

    def test_exact_edit_preview_matches_the_pending_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "note.txt").write_text("hello world\n", encoding="ascii")
            call = {"name": "edit_file", "path": "note.txt", "old_string": "world", "new_string": "dairack"}
            preview = runtime.tool_approval_diff(call, root)
            self.assertIn("-hello world", preview)
            self.assertIn("+hello dairack", preview)
            self.assertEqual((root / "note.txt").read_text(encoding="ascii"), "hello world\n")

    def test_checkpoint_rejects_paths_outside_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            with patch.object(runtime, "CHECKPOINT_DIR", Path(directory) / "checkpoints"):
                with self.assertRaisesRegex(ValueError, "outside cwd"):
                    runtime.create_checkpoint(root, ["../secret.txt"])
                with self.assertRaisesRegex(ValueError, "outside cwd"):
                    runtime.create_checkpoint(root, ["/etc/passwd"])
