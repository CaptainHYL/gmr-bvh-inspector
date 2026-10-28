# BVH 骨架检查器

独立桌面工具，用于导入 BVH、查看骨架，以及检查当前帧的关节和父子节点位姿。
入口是 `scripts/inspect_bvh.py`，不需要加载机器人模型、SMPL 模型或执行重定向。

## 安装和启动

在项目根目录执行：

```bash
conda activate gmr
python -m pip install -r requirements-bvh-inspector.txt
python scripts/inspect_bvh.py
```

需要隐藏手指时，使用新增入口：

```bash
python scripts/inspect_bvh_no_fingers.py
# 也可直接传入文件，参数与原版一致：
python scripts/inspect_bvh_no_fingers.py \
  data/kongtian_BVH/kongfu-normal_001.bvh_Skeleton0.bvh
```

这个版本保留 `LeftHand` / `RightHand`（或对应的 wrist 节点），隐藏手部以下的
手掌、手指和末端点，包括关节球、骨段、标签和层级树条目。手部在简化视图中作为叶节点，
父子节点面板也采用同一显示范围。原始 BVH 数据、通道、帧数及保留关节的位姿不改变。
支持常见 Left/Right/L/R 的 hand/wrist 命名和命名空间，并对常见手指名称作补充识别。
没有这些手部细节的文件正常显示。两个版本左下角的 XYZ 文字均使用亮色。

点击右上角“打开 BVH…”选择文件。也可以启动时传入路径：

```bash
python scripts/inspect_bvh.py \
  assets/xsens_bvh_test/251021_04_boxing_120Hz_cm_3DsMax.bvh

python scripts/inspect_bvh.py \
  --bvh_file data/kongtian_BVH/kongfu-normal_001.bvh_Skeleton0.bvh \
  --scale 0.01 --up-axis y
```

GUI 使用 PySide6 的 Core/Gui/Widgets/OpenGL 模块，因此依赖文件安装的是
`PySide6-Essentials`，无需下载 WebEngine 等模块。依赖是可选的，不影响原有 GMR 脚本。

本机已在 `gmr` 环境验证 Python 3.10、PySide6-Essentials 6.12.0、PyVista 0.46.5、
PyVistaQt 0.11.4 和 VTK 9.5.2，并已安装 Linux 桌面所需的 `xcb-util-cursor`。

## 操作

- 单击三维关节球，或者点击左侧层级树，选择一个节点。
- 导入时自动从人体正面查看：通过左右肩、左右髋的位置识别躯干正面，不依赖源文件的全局朝向。
  两个脚本均生效；相机按窗口比例框住完整骨架，原始位姿和属性面板中的数值保持不变。
- 左键拖动旋转，中键拖动平移，滚轮缩放；“重置视角”恢复当前帧人体的正面视角。
  播放过程中不会持续强制转向，仍可自由观察动作。
- “恢复初始正面”会暂停播放、回到第 1 帧，并将骨架重新居中到初始正面视角。
  当前关节选择、名称/末端显示、局部/世界参考系、单位设置、倍速和循环选项会保留。
- 粉色为选中节点，蓝色为父节点，绿色为所有直接子节点。普通关节为橙色，末端点为灰色。
- 右侧“位姿与父子关系”表显示当前节点、父节点、全部直接子节点的名称、ID、位置和四元数。
  点击其中一行可继续选中相应节点。没有父节点或子节点时会明确显示“无”。
- “位姿参考系”切换世界位姿与各节点相对自身父节点的局部位姿。根节点的局部参考系仍为世界。
- “原始 BVH 数据”页显示完整层级路径、静态 OFFSET 和当前帧各通道的原始值。
- “关节名称”控制三维名称显示；“选中坐标轴”显示该节点的 X 红 / Y 绿 / Z 蓝坐标轴。
- “末端点”控制 `End Site` 显示。它没有独立旋转通道，局部旋转为单位旋转，世界方向继承父节点。
- 底部支持播放/暂停、逐帧、拖动时间轴、输入帧号、倍速和循环。所选节点在换帧后保持不变。
- 空格播放/暂停，左右方向键逐帧，`Ctrl+O` 打开文件。点击三维关节会暂停，便于检查。
- 源数据帧率较高时，界面按实际时间跳到相应源帧显示；逐帧查看仍可访问每一帧。
- 文件在后台线程读取。关闭正在导入的窗口会取消导入，再安全关闭窗口。

帧编号在界面中从 **1** 开始，第一帧时间为 **0**，第 n 帧时间为 `(n-1) × Frame Time`。
界面显示的时长是最后一帧的时间戳 `(帧数-1) × Frame Time`。

## 坐标、单位和旋转约定

- BVH 不提供通用的长度单位声明，工具不猜测单位。默认源单位按厘米处理。
- “米 / 源单位”表示源位置乘以此系数后得到米：厘米填 `0.01`，毫米填 `0.001`，米填 `1`。
  命令行可用 `--scale` 设置；静态 OFFSET 和原始通道页始终保留源单位。
- 窗口始终以 Z 向上显示。“源上方向”默认 Y 向上；Z 向上的文件选择 Z。
  Y→Z 换基为 `(x, y, z) → (x, -z, y)`，保持右手坐标系。
- 所有世界、局部坐标基统一换基：`p' = scale × Bp`，`R' = B R Bᵀ`。
  表中的位姿和三维坐标轴采用同一约定。四元数顺序固定为 **w, x, y, z**。
- 局部变换为静态 `OFFSET` 后接文件声明顺序的通道变换，每个节点独立保留通道定义。
  根节点也保留 OFFSET。已有 `vis_bvh_motion.py` 使用的旧解析器会用运动平移覆盖根 OFFSET；
  对非零根 OFFSET 文件，两者世界位置可能有一个固定差值。
- “跟随根节点”只平移相机，不修改坐标或移除根运动；地面网格只是参考平面。
- 初始正面视角优先使用肩/髋的几何位置，支持常见 Left/Right/L/R、命名空间及 shoulder/collar/
  clavicle/arm、hip/upleg/thigh 命名。局部骨架可使用脚到脚趾的方向辅助；没有可识别人体标志时，
  使用根节点坐标轴作为确定性的回退，无法仅凭无语义节点保证区分正面与背面。
- 静态 OFFSET 只是骨架偏移，第一帧是文件中的第一帧动作，不假定为 T-pose。

## 支持的 BVH 和错误处理

动态解析节点数量和名称，保留命名空间、标点及重复名称。节点用内部 ID 区分，
图形选取使用 actor 与 ID 的映射，不通过名称或最近三维点猜测节点。

支持单个 ROOT、分支 JOINT、End Site，以及每个节点独立的 0–6 个 XYZ 平移/旋转通道。
每个运动帧应占一行。导入时校验通道名、每帧数值数量、声明帧数、有限数值及正的 Frame Time。
多 ROOT、缩放等扩展通道会明确报错。文件文本使用 UTF-8（含可选 BOM）。

## 检查与测试

数值和样例回归测试：

```bash
conda run -n gmr python -m unittest discover -s tests -p test_bvh_inspector.py -v
```

有桌面显示的环境中，启用 Qt/VTK 窗口交互测试：

```bash
BVH_INSPECTOR_GUI_TEST=1 conda run --no-capture-output -n gmr \
  python -m unittest discover -s tests -p test_bvh_inspector.py -v
```

Linux 显卡驱动无法创建 OpenGL 上下文时，可以使用软件渲染：

```bash
python scripts/inspect_bvh.py --software-rendering
```

如果 Qt 提示缺少 `xcb-cursor`，在 Ubuntu/Debian 上安装 `libxcb-cursor0`，
或在当前 Conda 环境安装 `xcb-util-cursor`。运行需要桌面显示服务。
