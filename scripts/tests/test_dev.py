from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[2]


class GitWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        for name in ("scripts", ".githooks"):
            (self.root / name).mkdir()
        shutil.copy2(SOURCE / "scripts/dev.py", self.root / "scripts/dev.py")
        shutil.copy2(SOURCE / ".githooks/prepare-commit-msg", self.root / ".githooks/prepare-commit-msg")
        self.git("init", "-q", "--initial-branch=KAN-63-example")
        self.git("config", "user.name", "Workflow Test")
        self.git("config", "user.email", "workflow@example.invalid")
        self.git("add", "scripts/dev.py", ".githooks/prepare-commit-msg")
        self.git("commit", "-qm", "Initial test fixture")

    def command(self, *args, check=True):
        return subprocess.run(args, cwd=self.root, env=self.env, text=True, capture_output=True, check=check)

    def git(self, *args, check=True):
        return self.command("git", *args, check=check)

    def dev(self, *args, check=True):
        return self.command(sys.executable, "scripts/dev.py", *args, check=check)

    def message(self):
        return self.git("log", "-1", "--format=%B").stdout.rstrip("\n")

    def test_setup_and_commit_from_git(self):
        self.dev("setup")
        self.dev("setup")
        self.git("commit", "--allow-empty", "-qm", "feat: 한국어 제목", "-m", "검증 결과 보존")
        self.assertEqual(self.message(), "KAN-63 feat: 한국어 제목\n\n검증 결과 보존")
        self.assertEqual(self.git("config", "--local", "core.hooksPath").stdout.strip(), ".githooks")

    def test_existing_key_is_not_duplicated(self):
        self.dev("setup")
        self.git("commit", "--allow-empty", "-qm", "KAN-63 feat: example")
        self.assertEqual(self.message(), "KAN-63 feat: example")

    def test_wrong_key_fails_without_commit(self):
        self.dev("setup")
        before = self.git("rev-parse", "HEAD").stdout
        result = self.git("commit", "--allow-empty", "-qm", "KAN-64 wrong ticket", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before, self.git("rev-parse", "HEAD").stdout)

    def test_numberless_branch_cannot_make_normal_commit(self):
        self.dev("setup")
        self.git("switch", "-c", "main")
        self.assertNotEqual(self.git("commit", "--allow-empty", "-qm", "missing key", check=False).returncode, 0)

    def test_multi_ticket_branch_keeps_both_keys(self):
        self.dev("setup")
        self.git("switch", "-c", "KAN-55-KAN-56-development-contracts")
        self.git("commit", "--allow-empty", "-qm", "contracts")
        self.assertEqual(self.message(), "KAN-55 KAN-56 contracts")

    def test_fixup_subject_is_preserved(self):
        self.dev("setup")
        self.git("commit", "--allow-empty", "-qm", "fixup! KAN-63 previous change")
        self.assertEqual(self.message(), "fixup! KAN-63 previous change")

    def test_crlf_body_and_dependency_reference_are_preserved(self):
        msg = self.root / ".git" / "message"
        msg.write_bytes("제목\r\n\r\n선행 작업 KAN-55\r\n".encode())
        self.dev("prepare-commit-msg", str(msg), "message")
        self.assertEqual(msg.read_bytes(), "KAN-63 제목\r\n\r\n선행 작업 KAN-55\r\n".encode())

    def test_merge_and_reused_messages_are_preserved(self):
        msg = self.root / ".git" / "message"
        for source in ("merge", "squash", "commit"):
            msg.write_text("KAN-64 historical message\n")
            self.dev("prepare-commit-msg", str(msg), source)
            self.assertEqual(msg.read_text(), "KAN-64 historical message\n")

    def test_setup_preserves_other_hooks(self):
        self.git("config", "core.hooksPath", "custom-hooks")
        self.assertNotEqual(self.dev("setup", check=False).returncode, 0)
        self.assertEqual(self.git("config", "core.hooksPath").stdout.strip(), "custom-hooks")

    def test_setup_preserves_default_existing_hook(self):
        hook = self.root / ".git/hooks/prepare-commit-msg"
        hook.write_text("#!/bin/sh\nexit 0\n")
        self.assertNotEqual(self.dev("setup", check=False).returncode, 0)
        self.assertEqual(hook.read_text(), "#!/bin/sh\nexit 0\n")

    def test_start_rejects_invalid_input_and_dirty_tree(self):
        self.assertNotEqual(self.dev("start", "KAN-0", "example", check=False).returncode, 0)
        self.assertNotEqual(self.dev("start", "KAN-63", "../main", check=False).returncode, 0)
        (self.root / "notes.txt").write_text("unfinished work")
        self.assertNotEqual(self.dev("start", "KAN-64", "example", check=False).returncode, 0)
        self.assertTrue((self.root / "notes.txt").exists())
        self.assertEqual(self.git("branch", "--show-current").stdout.strip(), "KAN-63-example")

    def test_start_uses_fetched_main_without_tracking_main(self):
        remote = Path(self.temp.name) / "origin.git"
        self.command("git", "init", "--bare", "-q", str(remote))
        self.git("remote", "add", "origin", str(remote))
        self.git("push", "-q", "origin", "HEAD:main")
        main = self.git("rev-parse", "HEAD").stdout.strip()
        self.dev("start", "KAN-64", "api-contract")
        self.assertEqual(self.git("branch", "--show-current").stdout.strip(), "KAN-64-api-contract")
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.strip(), main)
        self.assertNotEqual(self.git("rev-parse", "--abbrev-ref", "@{upstream}", check=False).returncode, 0)


if __name__ == "__main__":
    unittest.main()
