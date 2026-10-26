"""Launch the standalone desktop BVH skeleton inspector."""

import argparse
import math
import os
from pathlib import Path
import sys


def main(argv=None, *, hide_fingers=False):
    description = ("导入 BVH，保留手部节点，隐藏手掌和手指细节。" if hide_fingers else
                   "导入 BVH，点击检查关节及其父子节点的位姿。")
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("bvh", nargs="?", help="可选的 BVH 文件；不传则在窗口中选择。")
    parser.add_argument("--bvh_file", "--bvh-file", help="BVH 文件路径（也可使用位置参数）。")
    parser.add_argument("--scale", type=float, default=0.01, help="米/源长度单位；默认 0.01，即厘米转换到米。")
    parser.add_argument("--up-axis", choices=("y", "z"), default="y", help="源文件上方向，默认 y；窗口使用 Z 向上。")
    parser.add_argument("--software-rendering", action="store_true", help="Linux 下使用 Mesa 软件渲染。")
    args = parser.parse_args(argv)
    if args.bvh and args.bvh_file:
        parser.error("文件路径请只传一次。")
    if not math.isfinite(args.scale) or not 1e-8 <= args.scale <= 1e6:
        parser.error("--scale 必须在 1e-8 到 1e6 之间。")
    if args.software_rendering:
        os.environ["LIBGL_ALWAYS_SOFTWARE"] = "1"
        os.environ["__GLX_VENDOR_LIBRARY_NAME"] = "mesa"
    os.environ["QT_API"] = "pyside6"
    try:
        from PySide6.QtWidgets import QApplication
        from bvh_inspector.window import BVHInspector
    except ImportError as exc:
        requirements = Path(__file__).resolve().parents[1] / "requirements-bvh-inspector.txt"
        print(f"缺少 BVH 检查器依赖：{exc}\n请在当前 Python 环境安装：\n"
              f'  "{sys.executable}" -m pip install -r "{requirements}"', file=sys.stderr)
        return 1
    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setApplicationName("BVH 骨架检查器")
    window = BVHInspector(scale=args.scale, up_axis=args.up_axis, hide_fingers=hide_fingers)
    window.show()
    path = args.bvh or args.bvh_file
    if path:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: window.load_file(path))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
