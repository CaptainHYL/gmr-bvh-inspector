# GMR BVH 骨架检查器

给已有 [GMR](https://github.com/YanjieZe/GMR) 项目添加可交互的 BVH 查看窗口。
支持选择文件、播放/逐帧、点击关节查看局部/世界位姿与父子关系、隐藏手指，并在导入时自动从人体正面观察。
仅依赖 NumPy/SciPy、PySide6 和 PyVista，不需要机器人模型或 SMPL 模型。

## 在其他设备的 GMR 中一键部署

在 **GMR 项目目录** 打开终端，执行下面这一条命令（自动选择已有的 `gmr` Conda 环境）：

```bash
curl -fsSL https://raw.githubusercontent.com/CaptainHYL/gmr-bvh-inspector/main/install.sh | bash
```

安装程序会下载本仓库的检查器文件，在 `gmr` 环境安装 GUI 依赖，并在 Linux 的 Conda 环境补齐
缺少的 `xcb-util-cursor`。更新现有检查器文件之前会自动备份；不会覆盖 GMR 的 README、模型、
动作数据、重定向代码或配置，也不会修改 GMR 的 Git 远程地址。

安装完成后运行：

```bash
./bvh-inspector                    # 隐藏手指版，默认保留到 LeftHand / RightHand
./bvh-inspector-full               # 完整骨架版
./bvh-inspector /path/to/motion.bvh # 直接导入文件
```

启动器绑定安装时使用的 Python，之后无需重新激活 Conda 环境。
Windows 对应入口为 `bvh-inspector.cmd` 和 `bvh-inspector-full.cmd`。

如果 Conda 环境名不是 `gmr`，可使用 `bash -s --` 传递安装参数，或指定其他环境：

```bash
curl -fsSL https://raw.githubusercontent.com/CaptainHYL/gmr-bvh-inspector/main/install.sh \
  | BVH_INSPECTOR_ENV=你的环境名 bash
```

也可以用 `BVH_INSPECTOR_PYTHON=/path/to/python` 指定解释器。没有 Conda 时使用当前 Python。

## 其他安装方式

Windows 或不使用 Bash 的设备，仍可执行 Python 版本的一键命令：

```bash
conda run --no-capture-output -n gmr python -c "import urllib.request; exec(compile(urllib.request.urlopen('https://raw.githubusercontent.com/CaptainHYL/gmr-bvh-inspector/main/install.py', timeout=45).read(), '<bvh-inspector-installer>', 'exec'))"
```

克隆本仓库后部署到指定 GMR 目录：

```bash
git clone https://github.com/CaptainHYL/gmr-bvh-inspector.git
cd gmr-bvh-inspector
conda run --no-capture-output -n gmr python install.py \
  --target /path/to/GMR --source-dir .
```

使用 pip 镜像：

```bash
conda run --no-capture-output -n gmr python install.py \
  --target /path/to/GMR --source-dir . \
  --index-url https://mirrors.aliyun.com/pypi/simple
```

本仓库也可以独立启动：

```bash
python -m pip install -r requirements-bvh-inspector.txt
python scripts/inspect_bvh_no_fingers.py
```

使用 Python 3.10–3.13。运行窗口需要桌面显示服务。Linux 非 Conda 环境缺少 `libxcb-cursor` 时，
Ubuntu/Debian 可安装 `libxcb-cursor0`；显卡驱动有问题时可添加 `--software-rendering` 启动。
下载过程需要能够访问 `raw.githubusercontent.com` 和 `codeload.github.com`，并遵循当前代理环境设置。

## 更新与备份

再次运行一键部署命令即可更新。已经部署的 GMR 中也可执行：

```bash
bash scripts/install_bvh_inspector.sh
```

Shell 入口会自动选择 `gmr` 环境；Python 入口 `scripts/install_bvh_inspector.py` 也继续可用。
`--ref <标签或提交>` 可安装指定版本；`--skip-deps` 可在依赖已装好时跳过依赖安装与检查。
修改前的文件位于 GMR 的 `.bvh-inspector/backups/`，同版本重复安装不会新增备份。
安装只复制明确列出的检查器文件，并保留原始 BVH 通道、运动轨迹和位姿数值。

## 操作说明

- 左键单击关节球，或在层级树选择关节；右侧显示自身、父节点和直接子节点的名称与位姿。
- 左键拖动旋转，中键平移，滚轮缩放；“重置视角”恢复当前帧的正面观察。
- 空格播放/暂停，方向键逐帧，`Ctrl+O` 打开文件。
- 世界/局部参考系可切换；位置统一以米显示，四元数顺序为 WXYZ。
- 默认源单位为厘米、源坐标 Y 向上；可在界面调整，或传入 `--scale` / `--up-axis`。
- 隐藏手指版去除手掌、手指和手部末端的球、骨段及标签，保留手部节点。
- 人体正面主要依据肩/髋几何位置识别，不依赖厂商的根节点局部轴；没有可识别人体系标志的
  自定义骨架使用根坐标轴回退，无法单凭无语义节点保证区分正面与背面。

完整说明见 [docs/BVH_INSPECTOR.md](docs/BVH_INSPECTOR.md)。本仓库不包含用户的 BVH 文件或 GMR 模型。

## 验证

```bash
python -m unittest discover -s tests -v
# 已安装图形依赖且有桌面显示时：
BVH_INSPECTOR_GUI_TEST=1 python -m unittest discover -s tests -v
```

项目样例测试在相应 GMR 数据不可用时自动跳过。安装器测试覆盖目标目录检查、备份、重复安装、
失败回滚及压缩包文件白名单。

## License

采用 [MIT License](LICENSE)，保留 GMR 的版权声明。
