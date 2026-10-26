"""Installer boundaries, repeatability, archive handling and rollback."""

from io import BytesIO
import importlib.util
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bvh_install", ROOT / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="GMR deployment test ")
        self.addCleanup(self.temporary.cleanup)
        self.target = Path(self.temporary.name) / "GMR 测试目录"
        (self.target / "scripts").mkdir(parents=True)
        (self.target / "general_motion_retargeting").mkdir()

    def test_gmr_target_detection_does_not_accept_an_unrelated_directory(self):
        self.assertEqual(installer.find_gmr_root(self.target), self.target)
        with patch.object(Path, "cwd", return_value=self.target / "scripts"):
            self.assertEqual(installer.find_gmr_root(), self.target)
        with self.assertRaisesRegex(ValueError, "找不到 GMR"):
            installer.find_gmr_root(self.target.parent)

    def test_local_install_preserves_other_gmr_files_and_launchers_work(self):
        sentinel = self.target / "README.md"
        sentinel.write_text("Existing GMR README")
        payload = installer.collect_payload(ROOT)
        payload.update(installer.launcher_payload(self.target, sys.executable))
        installer.deploy_files(payload, self.target)
        self.assertEqual(sentinel.read_text(), "Existing GMR README")
        self.assertEqual((self.target / "scripts/bvh_inspector/camera.py").read_bytes(),
                         (ROOT / "scripts/bvh_inspector/camera.py").read_bytes())
        if os.name != "nt":
            for launcher in ("bvh-inspector", "bvh-inspector-full"):
                result = subprocess.run([str(self.target / launcher), "--help"], capture_output=True,
                                        text=True, check=True)
                self.assertIn("--bvh_file", result.stdout)

    def test_update_backs_up_changes_and_identical_install_is_a_noop(self):
        name = "scripts/inspect_bvh.py"
        file = self.target / name
        file.write_bytes(b"old user version")
        backup = installer.deploy_files({name: b"new version"}, self.target)
        self.assertEqual((backup / name).read_bytes(), b"old user version")
        self.assertEqual(file.read_bytes(), b"new version")
        self.assertIsNone(installer.deploy_files({name: b"new version"}, self.target))
        self.assertEqual(len(list((self.target / ".bvh-inspector/backups").iterdir())), 1)

    def test_mid_install_failure_restores_changed_files(self):
        existing = self.target / "scripts/existing.py"
        existing.write_bytes(b"original")
        replace = os.replace
        calls = []

        def fail_second(source, destination):
            calls.append(destination)
            if len(calls) == 2:
                raise OSError("simulated disk failure")
            return replace(source, destination)

        with patch.object(installer.os, "replace", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "simulated disk"):
                installer.deploy_files({"scripts/existing.py": b"updated",
                                        "scripts/new.py": b"new"}, self.target)
        self.assertEqual(existing.read_bytes(), b"original")
        self.assertFalse((self.target / "scripts/new.py").exists())

    def test_download_uses_explicit_manifest_without_extracting_zip_paths(self):
        data = BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            for name in installer.FILE_MAP:
                archive.writestr("gmr-bvh-inspector-main/" + name, name.encode())
            archive.writestr("gmr-bvh-inspector-main/../../unrelated.txt", "unwanted")
        with patch.object(installer.urllib.request, "urlopen", return_value=BytesIO(data.getvalue())):
            payload = installer.collect_payload(ref="main")
        self.assertEqual(set(payload), set(installer.FILE_MAP.values()))
        self.assertEqual(payload["scripts/inspect_bvh.py"], b"scripts/inspect_bvh.py")
        self.assertFalse((self.target.parent / "unrelated.txt").exists())

    def test_install_rejects_symlinks_leading_outside_target(self):
        if os.name == "nt":
            self.skipTest("Unix symlink boundary")
        outside = self.target.parent / "outside"
        outside.mkdir()
        (self.target / "scripts/link").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "目录之外"):
            installer.deploy_files({"scripts/link/other.py": b"data"}, self.target)
        self.assertFalse((outside / "other.py").exists())

    @unittest.skipIf(os.name == "nt", "Bash bootstrap")
    def test_short_bootstrap_selects_conda_forwards_options_and_cleans_up(self):
        bin_dir = Path(self.temporary.name) / "bin"
        bin_dir.mkdir()
        curl = bin_dir / "curl"
        curl.write_text('#!/usr/bin/env bash\ncp "$BVH_TEST_INSTALLER" "${@: -1}"\n')
        conda = bin_dir / "conda"
        conda.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" > "$BVH_TEST_CONDA_LOG"\n'
                         'shift 5\nexec "$BVH_TEST_PYTHON" "$@"\n')
        curl.chmod(0o755)
        conda.chmod(0o755)
        log = Path(self.temporary.name) / "conda.log"
        environment = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"],
                           TMPDIR=self.temporary.name, BVH_INSPECTOR_PYTHON="",
                           BVH_INSPECTOR_ENV="custom-gmr", BVH_TEST_INSTALLER=str(ROOT / "install.py"),
                           BVH_TEST_CONDA_LOG=str(log), BVH_TEST_PYTHON=sys.executable)
        command = ["bash", str(ROOT / "install.sh"), "--source-dir", str(ROOT),
                   "--target", str(self.target), "--skip-deps"]
        subprocess.run(command, env=environment, check=True, capture_output=True, text=True)
        self.assertTrue(log.read_text().startswith("run --no-capture-output -n custom-gmr python "))
        self.assertTrue((self.target / "scripts/install_bvh_inspector.sh").is_file())
        self.assertFalse(list(Path(self.temporary.name).glob("gmr-bvh-install.*")))
        log.unlink()
        curl.write_text("#!/usr/bin/env bash\nexit 22\n")
        result = subprocess.run(command, env=environment, capture_output=True)
        self.assertEqual(result.returncode, 22)
        self.assertFalse(log.exists())
        self.assertFalse(list(Path(self.temporary.name).glob("gmr-bvh-install.*")))


if __name__ == "__main__":
    unittest.main()
