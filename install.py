#!/usr/bin/env python3
"""Install/update the standalone BVH inspector inside an existing GMR checkout.

Uses only the Python standard library until optional dependencies are installed.
Can be executed directly from the raw GitHub URL without a local clone.
"""

import argparse
import ctypes
from datetime import datetime, timezone
from io import BytesIO
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile


REPOSITORY = "CaptainHYL/gmr-bvh-inspector"
DEFAULT_REF = "main"
FILE_MAP = {
    "scripts/inspect_bvh.py": "scripts/inspect_bvh.py",
    "scripts/inspect_bvh_no_fingers.py": "scripts/inspect_bvh_no_fingers.py",
    **{f"scripts/bvh_inspector/{name}.py": f"scripts/bvh_inspector/{name}.py"
       for name in ("__init__", "model", "window", "runtime", "filters", "camera")},
    "requirements-bvh-inspector.txt": "requirements-bvh-inspector.txt",
    "docs/BVH_INSPECTOR.md": "docs/BVH_INSPECTOR.md",
    "README.md": "docs/BVH_INSPECTOR_DEPLOYMENT.md",
    "LICENSE": "docs/BVH_INSPECTOR_LICENSE.txt",
    "install.py": "scripts/install_bvh_inspector.py",
    "install.sh": "scripts/install_bvh_inspector.sh",
}


def collect_payload(source_dir=None, ref=DEFAULT_REF):
    """Read only explicitly listed files; do not extract arbitrary ZIP entries."""
    if source_dir is not None:
        root = Path(source_dir).expanduser().resolve()
        return {destination: (root / source).read_bytes() for source, destination in FILE_MAP.items()}
    url = f"https://codeload.github.com/{REPOSITORY}/zip/{urllib.parse.quote(ref, safe='')}"
    print(f"下载检查器：{REPOSITORY} @ {ref}", flush=True)
    request = urllib.request.Request(url, headers={"User-Agent": "GMR-BVH-Inspector-Installer"})
    with urllib.request.urlopen(request, timeout=45) as response:
        archive_bytes = response.read(10 * 1024 * 1024 + 1)
    if len(archive_bytes) > 10 * 1024 * 1024:
        raise ValueError("下载的源码压缩包异常大，已停止安装。")
    with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
        roots = {name.split("/", 1)[0] for name in archive.namelist() if "/" in name}
        if len(roots) != 1:
            raise ValueError("源码压缩包目录结构异常。")
        prefix = roots.pop() + "/"
        return {destination: archive.read(prefix + source) for source, destination in FILE_MAP.items()}


def find_gmr_root(target=None):
    candidates = [Path(target).expanduser()] if target else [Path.cwd(), *Path.cwd().parents]
    for candidate in candidates:
        root = candidate.resolve()
        if (root / "general_motion_retargeting").is_dir() and (root / "scripts").is_dir():
            return root
    raise ValueError("找不到 GMR 项目。请在 GMR 目录运行，或传入 --target /你的/GMR/路径。")


def _cursor_available():
    for candidate in (Path(sys.prefix) / "lib/libxcb-cursor.so.0", "libxcb-cursor.so.0"):
        try:
            ctypes.CDLL(str(candidate), mode=ctypes.RTLD_GLOBAL)
            return True
        except OSError:
            pass
    return False


def install_dependencies(requirements, index_url=None):
    command = [sys.executable, "-m", "pip", "install", "-r", str(requirements)]
    if index_url:
        command.extend(["--index-url", index_url])
    print(f"安装依赖到当前 Python：{sys.executable}", flush=True)
    subprocess.run(command, check=True)
    if sys.platform.startswith("linux") and not _cursor_available():
        conda = os.environ.get("CONDA_EXE") or shutil.which("conda")
        if conda and (Path(sys.prefix) / "conda-meta").is_dir():
            print("安装 Qt 桌面依赖 xcb-util-cursor…", flush=True)
            subprocess.run([conda, "install", "--prefix", sys.prefix, "-c", "conda-forge",
                            "--freeze-installed", "--yes", "xcb-util-cursor"], check=True)
        if not _cursor_available():
            raise RuntimeError("缺少 Qt 的 libxcb-cursor。请激活 GMR 的 Conda 环境重试，"
                               "或在 Ubuntu/Debian 安装 libxcb-cursor0 后重试。")
    environment = os.environ.copy()
    environment["QT_API"] = "pyside6"
    subprocess.run([sys.executable, "-c", "from PySide6.QtWidgets import QApplication; "
                    "import numpy, scipy, pyvista, pyvistaqt; print('GUI 依赖检查通过')"],
                   env=environment, check=True)


def launcher_payload(target, python_executable):
    payload = {}
    for name, script in (("bvh-inspector", "inspect_bvh_no_fingers.py"),
                         ("bvh-inspector-full", "inspect_bvh.py")):
        entry = target / "scripts" / script
        if os.name == "nt":
            executable = str(python_executable).replace("%", "%%")
            entry_path = str(entry).replace("%", "%%")
            payload[name + ".cmd"] = (f'@echo off\r\nchcp 65001 >nul\r\n'
                                       f'"{executable}" "{entry_path}" %*\r\n').encode("utf-8")
        else:
            content = f"#!/usr/bin/env bash\nexec {shlex.quote(str(python_executable))} {shlex.quote(str(entry))} \"$@\"\n"
            payload[name] = content.encode("utf-8")
    return payload


def deploy_files(payload, target):
    """Back up changed files and restore them if a write fails mid-install."""
    target = Path(target).resolve()
    changed = []
    for name, data in payload.items():
        destination = target / name
        if not destination.resolve().is_relative_to(target):
            raise ValueError(f"安装路径指向 GMR 目录之外：{name}")
        if destination.exists() and not destination.is_file():
            raise ValueError(f"目标路径不是普通文件：{name}")
        if not destination.exists() or destination.read_bytes() != data:
            changed.append(name)
    if not changed:
        print("检查器文件已是当前版本，无需覆盖。", flush=True)
        return None
    backup = target / ".bvh-inspector/backups" / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8])
    previous = {}
    for name in changed:
        destination = target / name
        previous[name] = destination.exists()
        if destination.exists():
            saved = backup / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, saved)
    touched = []
    try:
        for name in changed:
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            descriptor, staging = tempfile.mkstemp(prefix=".bvh-install-", dir=destination.parent)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(payload[name])
                os.chmod(staging, 0o755 if name.startswith("bvh-inspector") else 0o644)
                os.replace(staging, destination)
                touched.append(name)
            finally:
                if os.path.exists(staging):
                    os.unlink(staging)
    except Exception:
        for name in reversed(touched):
            destination = target / name
            if previous[name]:
                shutil.copy2(backup / name, destination)
            else:
                destination.unlink()
        raise
    if any(previous.values()):
        print(f"旧文件备份：{backup}", flush=True)
        return backup
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description="将 BVH 骨架检查器一键安装到已有 GMR 项目。")
    parser.add_argument("--target", help="GMR 路径，默认从当前目录向上查找。")
    parser.add_argument("--ref", default=DEFAULT_REF, help="GitHub 分支、标签或提交，默认 main。")
    parser.add_argument("--source-dir", help="从本地检查器仓库安装，适用于离线更新。")
    parser.add_argument("--skip-deps", action="store_true", help="依赖已装好时跳过依赖安装和检查。")
    parser.add_argument("--index-url", help="可选的 pip 镜像地址。")
    args = parser.parse_args(argv)
    try:
        if not (3, 10) <= sys.version_info[:2] < (3, 14):
            raise RuntimeError("请使用 Python 3.10–3.13，建议通过 conda run -n gmr python 运行。")
        target = find_gmr_root(args.target)
        payload = collect_payload(args.source_dir, args.ref)
        with tempfile.TemporaryDirectory(prefix="bvh-inspector-deps-") as directory:
            requirements = Path(directory) / "requirements.txt"
            requirements.write_bytes(payload["requirements-bvh-inspector.txt"])
            if not args.skip_deps:
                install_dependencies(requirements, args.index_url)
        payload.update(launcher_payload(target, sys.executable))
        deploy_files(payload, target)
        print(f"\n部署成功：{target}")
        print("隐藏手指版：" + (".\\bvh-inspector.cmd" if os.name == "nt" else "./bvh-inspector"))
        print("完整骨架版：" + (".\\bvh-inspector-full.cmd" if os.name == "nt" else "./bvh-inspector-full"))
        print("启动器已绑定本次安装使用的 Python 环境，可直接传入 BVH 路径。")
        return 0
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, RuntimeError,
            subprocess.CalledProcessError, urllib.error.URLError) as exc:
        print(f"部署失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
