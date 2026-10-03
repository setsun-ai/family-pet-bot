"""/update and /rollback: unpacking a release, the folder swap, and who may run them. No network, no pip."""
import io
import tarfile
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from petbot import __version__, updater
from petbot.handlers import handle
from tests.support import FAMILY, Harness


def archive(files: dict[str, str], top="family-pet-bot-1.9.0") -> bytes:
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as tar:
        for name, text in files.items():
            body = text.encode()
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return data.getvalue()


RELEASE = {"petbot/__init__.py": '__version__ = "1.9.0"\n', "petbot/core.py": "# new\n",
           "requirements.txt": "aiogram==9.9\n", ".env.example": "BOT_TOKEN=\n", "tests/test_x.py": ""}


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        package = root / "petbot"
        package.mkdir()
        (package / "__init__.py").write_text('__version__ = "1.2.0"\n')
        (root / "requirements.txt").write_text("aiogram==1.0\n")
        (root / ".env").write_text("BOT_TOKEN=secret\n")
        for name, value in {"PACKAGE_DIR": package, "PROJECT_DIR": root, "WORK_DIR": root / ".update",
                            "PREVIOUS_DIR": root / ".update" / "previous"}.items():
            patcher = mock.patch.object(updater, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.root, self.package = root, package

    def tearDown(self):
        self.tmp.cleanup()

    def test_versions_compare_as_numbers(self):
        self.assertTrue(updater.newer("v1.10.0", "1.9.3"))
        self.assertFalse(updater.newer("v1.2.0", "1.2.0"))

    def test_unpack_takes_only_the_package_and_requirements(self):
        dest = self.root / "out"
        updater.unpack(archive(RELEASE), dest)
        self.assertTrue((dest / "petbot" / "core.py").is_file())
        self.assertTrue((dest / "requirements.txt").is_file())
        self.assertFalse((dest / ".env.example").exists())
        self.assertFalse((dest / "tests").exists())

    def test_unpack_refuses_paths_leaving_the_folder(self):
        with self.assertRaises(updater.UpdateError):
            updater.unpack(archive({"petbot/../../evil.py": "x"}), self.root / "out")
        self.assertFalse((self.root.parent / "evil.py").exists())

    def test_install_swaps_code_keeps_private_files_and_rollback_undoes_it(self):
        runs = []

        def run(args, timeout):
            runs.append(args[1:3])
            return True, "OK 1.9.0"

        with mock.patch.object(updater, "_get", return_value=archive(RELEASE)), \
                mock.patch.object(updater, "_run", side_effect=run):
            updater.install("v1.9.0")
        self.assertIn("1.9.0", (self.package / "__init__.py").read_text())
        self.assertEqual((self.root / "requirements.txt").read_text(), "aiogram==9.9\n")
        self.assertEqual((self.root / ".env").read_text(), "BOT_TOKEN=secret\n")
        self.assertEqual(runs[0], ["-m", "pip"])  # requirements changed -> pip before the selftest
        self.assertEqual(updater.previous_version(), "1.2.0")

        self.assertEqual(updater.rollback(), "1.2.0")
        self.assertIn("1.2.0", (self.package / "__init__.py").read_text())
        self.assertEqual((self.root / "requirements.txt").read_text(), "aiogram==1.0\n")
        self.assertEqual(updater.rollback(), "1.9.0")  # a second rollback goes forward again

    def test_failed_selftest_changes_nothing(self):
        with mock.patch.object(updater, "_get", return_value=archive(RELEASE)), \
                mock.patch.object(updater, "_run", side_effect=[(True, ""), (False, "ImportError: boom")]):
            with self.assertRaisesRegex(updater.UpdateError, "boom"):
                updater.install("v1.9.0")
        self.assertIn("1.2.0", (self.package / "__init__.py").read_text())
        self.assertIsNone(updater.previous_version())
        self.assertFalse((self.root / ".update" / "new").exists())

    def test_same_requirements_skip_pip(self):
        (self.root / "requirements.txt").write_text(RELEASE["requirements.txt"])
        with mock.patch.object(updater, "_get", return_value=archive(RELEASE)), \
                mock.patch.object(updater, "_run", return_value=(True, "OK 1.9.0")) as run:
            updater.install("v1.9.0")
        self.assertEqual(run.call_count, 1)

    def test_rollback_without_a_previous_version(self):
        self.assertIsNone(updater.rollback())


class UpdateCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = await Harness().open()
        patcher = mock.patch.object(updater, "request_restart")
        self.restart = patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        await self.h.close()

    async def test_only_the_owner_in_private(self):
        with mock.patch.object(updater, "latest_release") as latest:
            await handle(self.h.message("/update", uid=200, chat=FAMILY), self.h.app)
            await handle(self.h.message("/update", chat=FAMILY), self.h.app)
        latest.assert_not_called()
        self.assertIn("only for the owner", self.h.last_text())

    async def test_already_newest(self):
        with mock.patch.object(updater, "latest_release", return_value=f"v{__version__}"), \
                mock.patch.object(updater, "install") as install:
            await handle(self.h.message("/update"), self.h.app)
        install.assert_not_called()
        self.assertIn("newest version", self.h.last_text())
        self.restart.assert_not_called()

    async def test_installs_and_restarts(self):
        with mock.patch.object(updater, "latest_release", return_value="v99.0.0"), \
                mock.patch.object(updater, "install", return_value="OK 99.0.0"):
            await handle(self.h.message("/update"), self.h.app)
        self.assertIn("v99.0.0 installed", self.h.last_text())
        self.restart.assert_called_once()

    async def test_failure_is_reported_and_no_restart(self):
        with mock.patch.object(updater, "latest_release", return_value="v99.0.0"), \
                mock.patch.object(updater, "install", side_effect=updater.UpdateError("selftest:\nboom")):
            await handle(self.h.message("/update"), self.h.app)
        self.assertIn("boom", self.h.last_text())
        self.restart.assert_not_called()

    async def test_command_from_before_the_restart_is_skipped(self):
        old = self.h.message("/rollback").model_copy(update={"date": datetime.now(UTC) - timedelta(seconds=120)})
        with mock.patch("petbot.handlers.STARTED", datetime.now(UTC)), \
                mock.patch.object(updater, "rollback") as rollback:
            await handle(old, self.h.app)
        rollback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
