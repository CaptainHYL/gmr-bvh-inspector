"""Qt desktop UI and pickable PyVista skeleton rendering."""

from concurrent.futures import CancelledError
import os
from pathlib import Path
from threading import Event
from time import perf_counter

os.environ["QT_API"] = "pyside6"

from .runtime import load_conda_xcb_cursor

_cursor_library = load_conda_xcb_cursor()

import numpy as np
from scipy.spatial.transform import Rotation
from PySide6 import QtCore, QtGui, QtWidgets
import pyvista as pv
from pyvistaqt import QtInteractor
from vtkmodules.vtkRenderingCore import vtkCellPicker

from .model import display_basis, load_bvh
from .filters import hand_detail_indices
from .camera import front_camera_pose


NORMAL = "#f6ad55"
SELECTED = "#fb7185"
PARENT = "#38bdf8"
CHILD = "#4ade80"
END_SITE = "#94a3b8"
BONE = "#94a9c0"

STYLE = """
QMainWindow, QWidget#central { background: #0b1220; color: #e2e8f0; }
QWidget { color: #e2e8f0; font-size: 13px; }
QFrame#panel { background: #111c2e; border: 1px solid #25334a; border-radius: 8px; }
QLabel#title { font-size: 22px; font-weight: 600; color: #f8fafc; }
QLabel#section { font-size: 15px; font-weight: 600; color: #f8fafc; }
QLabel#muted { color: #94a3b8; }
QPushButton { background: #1d2d44; border: 1px solid #33445d; border-radius: 5px; padding: 6px 12px; }
QPushButton:hover { background: #2c425e; border-color: #38bdf8; }
QPushButton:disabled { color: #53647a; background: #111c2e; }
QPushButton#primary { background: #0369a1; border-color: #0284c7; color: white; }
QPushButton#primary:hover { background: #0284c7; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: #0b1424; border: 1px solid #33445d;
  border-radius: 4px; padding: 5px; selection-background-color: #075985; }
QComboBox QAbstractItemView { background: #111c2e; selection-background-color: #075985; }
QTreeWidget, QTableWidget, QPlainTextEdit { background: #0d1727; border: 1px solid #25334a;
  gridline-color: #25334a; selection-background-color: #164e63; selection-color: #f8fafc; }
QHeaderView::section { background: #1a2940; color: #b8c8dd; border: none;
  border-right: 1px solid #25334a; padding: 7px 5px; }
QTreeWidget::item { padding: 4px 1px; }
QTabWidget::pane { border: 1px solid #25334a; }
QTabBar::tab { background: #132037; padding: 8px 14px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { background: #1a2b44; color: #7dd3fc; border-bottom-color: #38bdf8; }
QStatusBar { background: #111c2e; color: #a7bdd5; border-top: 1px solid #25334a; }
QSplitter::handle { background: #0b1220; }
QSlider::groove:horizontal { background: #26364d; height: 5px; border-radius: 2px; }
QSlider::handle:horizontal { background: #38bdf8; width: 13px; margin: -5px 0; border-radius: 6px; }
QSlider::sub-page:horizontal { background: #0369a1; }
QCheckBox { spacing: 6px; }
QToolTip { background: #1d2d44; color: #f8fafc; border: 1px solid #475569; }
"""


class _Loader(QtCore.QThread):
    loaded = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, path, parent):
        super().__init__(parent)
        self.path = path
        self.cancelled = Event()

    def run(self):
        try:
            motion = load_bvh(self.path, self.cancelled.is_set)
            if not self.cancelled.is_set():
                self.loaded.emit(motion)
        except CancelledError:
            pass
        except (OSError, ValueError, RecursionError, MemoryError) as exc:
            self.failed.emit(str(exc) or type(exc).__name__)


def _label(text="", name=None):
    label = QtWidgets.QLabel(text)
    label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
    if name:
        label.setObjectName(name)
    return label


class BVHInspector(QtWidgets.QMainWindow):
    def __init__(self, scale=0.01, up_axis="y", hide_fingers=False):
        super().__init__()
        self._hide_fingers = hide_fingers
        self._hidden_nodes = frozenset()
        self._app_title = "BVH 骨架检查器" + (" · 隐藏手指" if hide_fingers else "")
        self.motion = None
        self.frame = 0
        self.selected = None
        self.source_pose = None
        self.display_pose = None
        self._loader = None
        self._close_after_load = False
        self._joint_actors = {}
        self._actor_ids = {}
        self._axis_actors = []
        self._tree_items = {}
        self._last_root = None
        self._click_start = None
        self._playing = False
        self.setWindowTitle(self._app_title)
        self.resize(1500, 940)
        self.setMinimumSize(1120, 720)
        self.setStyleSheet(STYLE)
        self.setFont(QtGui.QFont("Noto Sans CJK SC", 10))
        self._build_ui(scale, up_axis)
        self.timer = QtCore.QTimer(self)
        self.timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self._picker = vtkCellPicker()
        self._picker.SetTolerance(0.008)
        self._picker.PickFromListOn()
        self.plotter.interactor.installEventFilter(self)
        self.plotter.set_background("#0b1424", top="#172b45")
        self.plotter.add_axes(color="#f8fafc", x_color="#ff6b6b", y_color="#4ade80", z_color="#60a5fa")
        self.plotter.add_text("Open a BVH file to begin", position="upper_left",
                              font_size=13, color="#94a3b8", name="empty")
        for key, callback in (("Space", self.toggle_play), ("Left", lambda: self.step(-1)),
                              ("Right", lambda: self.step(1)), ("Ctrl+O", self.open_file)):
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(key), self)
            shortcut.activated.connect(callback)
        self.statusBar().showMessage("打开 BVH 文件开始检查；默认按厘米转换到米，源坐标 Y 向上。")
        self._set_controls_enabled(False)

    def _build_ui(self, scale, up_axis):
        central = QtWidgets.QWidget(objectName="central")
        self.setCentralWidget(central)
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(16, 14, 16, 8)
        outer.setSpacing(10)
        heading = QtWidgets.QHBoxLayout()
        heading.addWidget(_label(self._app_title, "title"))
        heading.addStretch()
        self.open_button = QtWidgets.QPushButton("打开 BVH…", objectName="primary")
        self.open_button.clicked.connect(self.open_file)
        heading.addWidget(self.open_button)
        outer.addLayout(heading)
        self.file_info = _label("导入原始骨架，点击关节查看当前帧的位姿与父子关系。", "muted")
        self.file_info.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.file_info)

        options = QtWidgets.QHBoxLayout()
        self.reset_button = QtWidgets.QPushButton("重置视角")
        self.reset_button.clicked.connect(self.reset_camera)
        options.addWidget(self.reset_button)
        self.names_check = QtWidgets.QCheckBox("关节名称")
        self.axes_check = QtWidgets.QCheckBox("选中坐标轴")
        self.axes_check.setChecked(True)
        self.ends_check = QtWidgets.QCheckBox("末端点")
        self.ends_check.setChecked(True)
        self.follow_check = QtWidgets.QCheckBox("跟随根节点")
        for widget in (self.names_check, self.axes_check, self.ends_check, self.follow_check):
            options.addWidget(widget)
        self.names_check.toggled.connect(self._refresh_display)
        self.axes_check.toggled.connect(self._refresh_display)
        self.ends_check.toggled.connect(self._end_visibility_changed)
        self.follow_check.toggled.connect(lambda _: setattr(self, "_last_root", None))
        options.addStretch()
        options.addWidget(_label("源上方向"))
        self.up_combo = QtWidgets.QComboBox()
        self.up_combo.addItem("Y 向上", "y")
        self.up_combo.addItem("Z 向上", "z")
        self.up_combo.setCurrentIndex(0 if up_axis == "y" else 1)
        self.up_combo.currentIndexChanged.connect(self._coordinate_changed)
        options.addWidget(self.up_combo)
        options.addWidget(_label("米 / 源单位"))
        self.scale_spin = QtWidgets.QDoubleSpinBox()
        self.scale_spin.setDecimals(8)
        self.scale_spin.setRange(1e-8, 1e6)
        self.scale_spin.setSingleStep(0.001)
        self.scale_spin.setValue(scale)
        self.scale_spin.setToolTip("源位置乘以此值后得到米：厘米填 0.01，毫米填 0.001，米填 1。")
        self.scale_spin.setFixedWidth(130)
        self.scale_spin.setKeyboardTracking(False)
        self.scale_spin.valueChanged.connect(self._coordinate_changed)
        options.addWidget(self.scale_spin)
        outer.addLayout(options)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        outer.addWidget(splitter, 1)
        left = QtWidgets.QFrame(objectName="panel")
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.addWidget(_label("骨架层级", "section"))
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("搜索关节名称…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_tree)
        left_layout.addWidget(self.search)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(13)
        self.tree.currentItemChanged.connect(self._tree_selected)
        left_layout.addWidget(self.tree)
        left.setMinimumWidth(180)
        splitter.addWidget(left)

        viewport = QtWidgets.QWidget()
        viewport_layout = QtWidgets.QVBoxLayout(viewport)
        viewport_layout.setContentsMargins(0, 0, 0, 0)
        self.plotter = QtInteractor(viewport, auto_update=False)
        viewport_layout.addWidget(self.plotter.interactor, 1)
        help_label = _label("单击选取 · 左键拖动旋转 · 中键平移 · 滚轮缩放\n"
                            "粉：选中节点    蓝：父节点    绿：直接子节点    灰：末端点", "muted")
        help_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        viewport_layout.addWidget(help_label)
        splitter.addWidget(viewport)

        right = QtWidgets.QFrame(objectName="panel")
        right_layout = QtWidgets.QVBoxLayout(right)
        self.selection_title = _label("尚未选择关节", "section")
        self.selection_title.setWordWrap(True)
        self.selection_title.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        right_layout.addWidget(self.selection_title)
        self.selection_meta = _label("在骨架或左侧层级树中选择节点。", "muted")
        self.selection_meta.setWordWrap(True)
        right_layout.addWidget(self.selection_meta)
        frame_row = QtWidgets.QHBoxLayout()
        frame_row.addWidget(_label("位姿参考系"))
        self.reference_combo = QtWidgets.QComboBox()
        self.reference_combo.addItem("世界坐标系", "world")
        self.reference_combo.addItem("各节点自己的父坐标系", "local")
        self.reference_combo.currentIndexChanged.connect(self._update_panel)
        frame_row.addWidget(self.reference_combo, 1)
        right_layout.addLayout(frame_row)
        self.reference_note = _label("位置单位：米；四元数顺序：w, x, y, z。", "muted")
        self.reference_note.setWordWrap(True)
        right_layout.addWidget(self.reference_note)
        tabs = QtWidgets.QTabWidget()
        self.relations = QtWidgets.QTableWidget(0, 3)
        self.relations.setHorizontalHeaderLabels(["关系 / 节点", "位置 XYZ · 米", "四元数 WXYZ"])
        self.relations.verticalHeader().hide()
        self.relations.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.relations.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.relations.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.relations.setWordWrap(True)
        header = self.relations.horizontalHeader()
        header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setMinimumSectionSize(110)
        self.relations.cellClicked.connect(self._relation_selected)
        tabs.addTab(self.relations, "位姿与父子关系")
        self.raw_data = QtWidgets.QPlainTextEdit()
        self.raw_data.setReadOnly(True)
        tabs.addTab(self.raw_data, "原始 BVH 数据")
        right_layout.addWidget(tabs, 1)
        relation_hint = _label("点击表格中的父节点或子节点，可继续沿骨架查看。", "muted")
        relation_hint.setWordWrap(True)
        right_layout.addWidget(relation_hint)
        right.setMinimumWidth(380)
        splitter.addWidget(right)
        splitter.setSizes([220, 740, 480])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setChildrenCollapsible(False)

        playback = QtWidgets.QHBoxLayout()
        self.prev_button = QtWidgets.QPushButton("◀ 上一帧")
        self.play_button = QtWidgets.QPushButton("播放")
        self.next_button = QtWidgets.QPushButton("下一帧 ▶")
        self.prev_button.clicked.connect(lambda: self.step(-1))
        self.play_button.clicked.connect(self.toggle_play)
        self.next_button.clicked.connect(lambda: self.step(1))
        for button in (self.prev_button, self.play_button, self.next_button):
            playback.addWidget(button)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderPressed.connect(self.pause)
        self.slider.valueChanged.connect(self.seek)
        playback.addWidget(self.slider, 1)
        playback.addWidget(_label("帧"))
        self.frame_spin = QtWidgets.QSpinBox()
        self.frame_spin.setRange(1, 1)
        self.frame_spin.setKeyboardTracking(False)
        self.frame_spin.valueChanged.connect(lambda value: self.seek(value - 1))
        playback.addWidget(self.frame_spin)
        self.time_label = _label("0 / 0 · 0.000 s", "muted")
        self.time_label.setMinimumWidth(180)
        playback.addWidget(self.time_label)
        self.speed_combo = QtWidgets.QComboBox()
        for speed in (0.25, 0.5, 1., 2., 4.):
            self.speed_combo.addItem(f"{speed:g}×", speed)
        self.speed_combo.setCurrentIndex(2)
        self.speed_combo.currentIndexChanged.connect(self._speed_changed)
        playback.addWidget(self.speed_combo)
        self.loop_check = QtWidgets.QCheckBox("循环")
        playback.addWidget(self.loop_check)
        outer.addLayout(playback)

    def _set_controls_enabled(self, enabled):
        for control in (self.prev_button, self.play_button, self.next_button, self.slider,
                        self.frame_spin, self.reset_button, self.names_check, self.axes_check,
                        self.ends_check, self.follow_check, self.tree, self.search, self.reference_combo):
            control.setEnabled(enabled)
        if enabled and self.motion.frame_count == 1:
            self.play_button.setEnabled(False)

    def open_file(self):
        if self._loader:
            return
        self.pause()
        directory = str(self.motion.path.parent) if self.motion else str(Path.cwd())
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择 BVH 动作文件", directory,
                                                       "BVH 动作文件 (*.bvh *.BVH);;所有文件 (*)")
        if path:
            self.load_file(path)

    def load_file(self, path):
        if self._loader:
            return
        self.pause()
        self.open_button.setEnabled(False)
        self._set_controls_enabled(False)
        self.statusBar().showMessage(f"正在导入：{path}")
        self._loader = _Loader(path, self)
        self._loader.loaded.connect(self.set_motion)
        self._loader.failed.connect(self._load_failed)
        self._loader.finished.connect(self._load_finished)
        self._loader.finished.connect(self._loader.deleteLater)
        self._loader.start()

    def _load_failed(self, message):
        if not self._close_after_load:
            QtWidgets.QMessageBox.critical(self, "BVH 导入失败", message)
            self.statusBar().showMessage(f"导入失败：{message}")

    def _load_finished(self):
        self._loader = None
        self.open_button.setEnabled(True)
        self._set_controls_enabled(self.motion is not None)
        if self._close_after_load:
            self.close()

    def set_motion(self, motion):
        """Install parsed data on the GUI thread; also used by integration tests."""
        if self._close_after_load:
            return
        self.pause()
        self.motion = motion
        self._hidden_nodes = hand_detail_indices(motion) if self._hide_fingers else frozenset()
        self.frame = 0
        self.selected = 0
        self._last_root = None
        self.source_pose = motion.pose(0)
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        retained = [node for node in motion.joints if node.index not in self._hidden_nodes]
        real_count = sum(not node.is_end_site for node in retained)
        end_count = len(retained) - real_count
        hidden_note = f"  ·  已隐藏 {len(self._hidden_nodes)} 个手部细节节点" if self._hide_fingers else ""
        self.file_info.setText(f"{motion.path.name}  ·  {real_count} 关节 + {end_count} 末端点"
                               f"  ·  {motion.frame_count} 帧  ·  {motion.fps:.3f} Hz"
                               f"  ·  末帧时间 {motion.duration:.3f} s" + hidden_note)
        self.file_info.setToolTip(str(motion.path))
        self.setWindowTitle(f"{motion.path.name} — {self._app_title}")
        self.tree.blockSignals(True)
        self.tree.clear()
        self._tree_items = {}
        for node in motion.joints:
            suffix = "  [末端]" if node.is_end_site else ""
            item = QtWidgets.QTreeWidgetItem([node.name + suffix])
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, node.index)
            item.setToolTip(0, motion.node_path(node.index))
            if node.is_end_site:
                item.setForeground(0, QtGui.QColor(END_SITE))
            self._tree_items[node.index] = item
            if node.parent < 0:
                self.tree.addTopLevelItem(item)
            else:
                self._tree_items[node.parent].addChild(item)
        self.tree.expandAll()
        self.tree.setCurrentItem(self._tree_items[0])
        self.tree.blockSignals(False)
        for widget, low, high in ((self.slider, 0, motion.frame_count - 1),
                                  (self.frame_spin, 1, motion.frame_count)):
            widget.blockSignals(True)
            widget.setRange(low, high)
            widget.setValue(low)
            widget.blockSignals(False)
        self._set_controls_enabled(True)
        self._create_scene()
        self._filter_tree()
        self._refresh_display()
        self.reset_camera()

    def _create_scene(self):
        self.plotter.clear()
        self._joint_actors.clear()
        self._actor_ids.clear()
        self._picker.InitializePickList()
        sphere = pv.Sphere(radius=1, theta_resolution=14, phi_resolution=14)
        for node in self.motion.joints:
            if node.index in self._hidden_nodes:
                continue
            actor = self.plotter.add_mesh(sphere, name=f"joint-{node.index}", color=NORMAL,
                                          smooth_shading=True, pickable=True, reset_camera=False,
                                          render=False)
            self._joint_actors[node.index] = actor
            self._actor_ids[actor.GetAddressAsString("")] = node.index
            self._picker.AddPickList(actor)
        self._axis_actors = []
        for direction, color in zip(np.eye(3), ("#ff5555", "#44dd77", "#5599ff")):
            actor = self.plotter.add_mesh(pv.Arrow(direction=direction), color=color,
                                          pickable=False, reset_camera=False, render=False)
            self._axis_actors.append(actor)
        self._rebuild_bones()
        self._build_grid()

    def _node_visible(self, index):
        return index not in self._hidden_nodes and (
            self.ends_check.isChecked() or not self.motion.joints[index].is_end_site)

    def _visible_children(self, index):
        return tuple(child for child in self.motion.joints[index].children if child not in self._hidden_nodes)

    def _rebuild_bones(self):
        self._edges = [(node.parent, node.index) for node in self.motion.joints
                       if node.parent >= 0 and self._node_visible(node.index)
                       and node.parent not in self._hidden_nodes]
        self._bones = pv.PolyData(np.zeros((len(self.motion.joints), 3)))
        if self._edges:
            self._bones.lines = np.array([[2, a, b] for a, b in self._edges]).ravel()
            # PolyData initially has vertex cells; remove these before adding line colors.
            self._bones.verts = np.array([], dtype=np.int64)
            self._bones.cell_data["rgb"] = np.tile([148, 169, 192], (len(self._edges), 1)).astype(np.uint8)
            self.plotter.add_mesh(self._bones, scalars="rgb", rgb=True, line_width=5,
                                  render_lines_as_tubes=True, pickable=False, name="bones",
                                  reset_camera=False, render=False)
        else:
            self.plotter.remove_actor("bones", reset_camera=False, render=False)

    def _build_grid(self):
        pose = self.source_pose.transformed(display_basis(self.up_combo.currentData()), self.scale_spin.value())
        points = pose.world_positions[[node.index for node in self.motion.joints
                                       if node.index not in self._hidden_nodes]]
        self._body_size = max(float(np.ptp(points, axis=0).max()), 0.05)
        self._radius = self._body_size * 0.011
        size = self._body_size * 5
        center = [points[0, 0], points[0, 1], 0.]
        grid = pv.Plane(center=center, direction=(0, 0, 1), i_size=size, j_size=size,
                        i_resolution=20, j_resolution=20)
        self.plotter.add_mesh(grid, style="wireframe", color="#2c4058", line_width=1,
                              pickable=False, name="floor", reset_camera=False, render=False)

    def _refresh_display(self, *_):
        if self.motion is None:
            return
        self.display_pose = self.source_pose.transformed(
            display_basis(self.up_combo.currentData()), self.scale_spin.value())
        positions = self.display_pose.world_positions
        selected = self.motion.joints[self.selected]
        for index, actor in self._joint_actors.items():
            node = self.motion.joints[index]
            color = (SELECTED if node.index == self.selected else PARENT if node.index == selected.parent
                     else CHILD if node.index in selected.children else END_SITE if node.is_end_site else NORMAL)
            actor.position = positions[node.index]
            actor.scale = self._radius * (1.4 if node.index == self.selected else 0.7 if node.is_end_site else 1.)
            actor.prop.color = color
            actor.visibility = self._node_visible(node.index)
        if self._edges:
            self._bones.points = positions.copy()
            colors = []
            for parent, child in self._edges:
                color = PARENT if child == self.selected else CHILD if parent == self.selected else BONE
                colors.append(np.rint(np.array(pv.Color(color).float_rgb) * 255).astype(np.uint8))
            self._bones.cell_data["rgb"] = np.array(colors, dtype=np.uint8)
        for actor in self._axis_actors:
            matrix = np.eye(4)
            matrix[:3, :3] = self.display_pose.world_rotations[self.selected] * self._body_size * 0.12
            matrix[:3, 3] = positions[self.selected]
            actor.user_matrix = matrix
            actor.visibility = self.axes_check.isChecked()
        self.plotter.remove_actor("joint-labels", reset_camera=False, render=False)
        self.plotter.remove_actor("joint-labels-points", reset_camera=False, render=False)
        if self.names_check.isChecked():
            ids = [node.index for node in self.motion.joints if self._node_visible(node.index)]
            self.plotter.add_point_labels(positions[ids], [self.motion.joints[i].name for i in ids],
                                          name="joint-labels", show_points=False, font_size=12,
                                          text_color="#f1f5f9", shape_color="#172b45",
                                          shape_opacity=0.8, always_visible=False, pickable=False,
                                          reset_camera=False, render=False)
        root = positions[0]
        if self.follow_check.isChecked() and self._last_root is not None:
            delta = root - self._last_root
            self.plotter.camera.position = np.array(self.plotter.camera.position) + delta
            self.plotter.camera.focal_point = np.array(self.plotter.camera.focal_point) + delta
        self._last_root = root.copy()
        self.plotter.reset_camera_clipping_range()
        self._update_panel()
        self.time_label.setText(f"{self.frame + 1} / {self.motion.frame_count} · {self.frame * self.motion.frame_time:.3f} s")
        self.plotter.render()

    def _update_panel(self, *_):
        if self.motion is None or self.display_pose is None:
            return
        node = self.motion.joints[self.selected]
        kind = "末端点（无独立旋转通道）" if node.is_end_site else "ROOT" if node.parent < 0 else "JOINT"
        self.selection_title.setText(node.name)
        children = self._visible_children(self.selected)
        self.selection_meta.setText(f"ID #{node.index} · {kind} · {len(children)} 个直接子节点")
        self.selection_title.setToolTip(self.motion.node_path(self.selected))
        local = self.reference_combo.currentData() == "local"
        positions = self.display_pose.local_positions if local else self.display_pose.world_positions
        rotations = self.display_pose.local_rotations if local else self.display_pose.world_rotations
        self.reference_note.setText("单位：米；四元数 WXYZ。局部值各自相对其父节点，根节点相对世界。"
                                   if local else "单位：米；四元数 WXYZ。所有节点均相对同一世界坐标系。")
        related = [("当前节点", node.index, SELECTED)]
        related += [("父节点", node.parent, PARENT)] if node.parent >= 0 else [("父节点：无", None, PARENT)]
        related += [("直接子节点", index, CHILD) for index in children]
        if not children:
            related.append(("子节点：无", None, CHILD))
        self.relations.setRowCount(len(related))
        for row, (relation, index, color) in enumerate(related):
            if index is None:
                values = [relation, "—", "—"]
            else:
                relative = self.motion.joints[index]
                quaternion = Rotation.from_matrix(rotations[index]).as_quat()[[3, 0, 1, 2]]
                if quaternion[0] < 0:
                    quaternion *= -1
                values = [f"{relation}\n{relative.name}\n#{index}",
                          self._vector_text("xyz", positions[index]),
                          self._vector_text("wxyz", quaternion)]
            for column, text in enumerate(values):
                item = self.relations.item(row, column)
                if item is None:
                    item = QtWidgets.QTableWidgetItem()
                    self.relations.setItem(row, column, item)
                item.setText(text)
                item.setData(QtCore.Qt.ItemDataRole.UserRole, index)
                item.setForeground(QtGui.QColor(color if column == 0 else "#e2e8f0"))
                if index is not None:
                    item.setToolTip(self.motion.node_path(index))
                item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter)
            self.relations.setRowHeight(row, 100 if index is not None else 36)
        raw = [f"节点：{node.name}", f"内部 ID：{node.index}", f"路径：{self.motion.node_path(node.index)}",
               "", "静态 OFFSET（源长度单位）：", "  " + ", ".join(f"{x:.8g}" for x in node.offset), "",
               "当前帧原始通道（平移为源长度单位，旋转为度）："]
        raw.extend(f"  {channel}: {value:.8g}" for channel, value in
                   zip(node.channels, self.motion.channel_values(self.frame, self.selected)))
        if not node.channels:
            raw.append("  无运动通道；末端点方向继承父节点。" if node.is_end_site else "  无运动通道。")
        self.raw_data.setPlainText("\n".join(raw))
        world_p = self.display_pose.world_positions[self.selected]
        self.statusBar().showMessage(f"{node.name} [#{node.index}]  |  帧 {self.frame + 1}/{self.motion.frame_count}"
                                     f"  |  t={self.frame * self.motion.frame_time:.3f} s"
                                     f"  |  世界 XYZ=({world_p[0]:.5f}, {world_p[1]:.5f}, {world_p[2]:.5f}) m")

    @staticmethod
    def _vector_text(labels, values):
        return "\n".join(f"{axis}  {0. if abs(value) < 0.0000005 else value: .6f}"
                         for axis, value in zip(labels, values))

    def select_joint(self, index):
        if self.motion is None or not 0 <= index < len(self.motion.joints):
            return
        while index in self._hidden_nodes:
            index = self.motion.joints[index].parent
        self.selected = index
        if self.motion.joints[index].is_end_site and not self.ends_check.isChecked():
            self.ends_check.setChecked(True)
        self.tree.blockSignals(True)
        self.tree.setCurrentItem(self._tree_items[index])
        self.tree.scrollToItem(self._tree_items[index])
        self.tree.blockSignals(False)
        self._refresh_display()

    def _tree_selected(self, item, _previous):
        if item is not None:
            self.select_joint(item.data(0, QtCore.Qt.ItemDataRole.UserRole))

    def _relation_selected(self, row, column):
        index = self.relations.item(row, column).data(QtCore.Qt.ItemDataRole.UserRole)
        if index is not None:
            self.select_joint(index)

    def _filter_tree(self, *_):
        if self.motion is None:
            return
        query = self.search.text().strip().casefold()
        for node in reversed(self.motion.joints):
            allowed = self._node_visible(node.index)
            item = self._tree_items[node.index]
            visible_child = any(not self._tree_items[child].isHidden() for child in node.children)
            visible = allowed and (not query or query in node.name.casefold() or visible_child)
            item.setHidden(not visible)
            if query and visible_child:
                item.setExpanded(True)

    def _end_visibility_changed(self, *_):
        if self.motion is None:
            return
        if not self.ends_check.isChecked() and self.motion.joints[self.selected].is_end_site:
            self.selected = self.motion.joints[self.selected].parent
            self.tree.blockSignals(True)
            self.tree.setCurrentItem(self._tree_items[self.selected])
            self.tree.blockSignals(False)
        self._rebuild_bones()
        self._filter_tree()
        self._refresh_display()

    def _coordinate_changed(self, *_):
        if self.motion is None:
            return
        self._last_root = None
        self._build_grid()
        self._refresh_display()
        self.reset_camera()

    def reset_camera(self):
        if self.display_pose is None:
            return
        visible = [node.index for node in self.motion.joints if self._node_visible(node.index)]
        width, height = self.plotter.render_window.GetSize()
        position, target, up = front_camera_pose(
            self.motion, self.display_pose, visible, width / max(height, 1), self.plotter.camera.view_angle)
        self.plotter.camera.focal_point = target
        self.plotter.camera.position = position
        self.plotter.camera.up = up
        self.plotter.reset_camera_clipping_range()
        self.plotter.render()

    def eventFilter(self, watched, event):
        if watched is self.plotter.interactor:
            if event.type() == QtCore.QEvent.Type.MouseButtonPress and event.button() == QtCore.Qt.MouseButton.LeftButton:
                self._click_start = event.position()
            elif event.type() == QtCore.QEvent.Type.MouseButtonRelease and event.button() == QtCore.Qt.MouseButton.LeftButton:
                if self._click_start is not None and (event.position() - self._click_start).manhattanLength() < 5:
                    width, height = self.plotter.render_window.GetSize()
                    x = int(event.position().x() * width / max(watched.width(), 1))
                    y = height - 1 - int(event.position().y() * height / max(watched.height(), 1))
                    QtCore.QTimer.singleShot(0, lambda: self._pick_at(x, y))
                self._click_start = None
        return super().eventFilter(watched, event)

    def _pick_at(self, x, y):
        if self.motion is None:
            return
        self._picker.Pick(x, y, 0, self.plotter.renderer)
        actor = self._picker.GetActor()
        if actor is not None:
            index = self._actor_ids.get(actor.GetAddressAsString(""))
            if index is not None:
                self.pause()
                self.select_joint(index)

    def _set_frame(self, frame):
        self.frame = int(frame)
        self.source_pose = self.motion.pose(self.frame)
        for widget, value in ((self.slider, self.frame), (self.frame_spin, self.frame + 1)):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)
        self._refresh_display()

    def seek(self, frame):
        if self.motion is not None and 0 <= frame < self.motion.frame_count:
            self.pause()
            self._set_frame(frame)

    def step(self, direction):
        if self.motion is not None:
            self.seek(min(max(self.frame + direction, 0), self.motion.frame_count - 1))

    def pause(self):
        self._playing = False
        self.timer.stop()
        self.play_button.setText("播放")

    def toggle_play(self):
        if self.motion is None or self._loader or self.motion.frame_count <= 1:
            return
        if self._playing:
            self.pause()
        else:
            if self.frame == self.motion.frame_count - 1:
                self._set_frame(0)
            self._playing = True
            self._anchor_frame = self.frame
            self._anchor_time = perf_counter()
            self.play_button.setText("暂停")
            self.timer.start()

    def _speed_changed(self, *_):
        if self._playing:
            self._anchor_frame = self.frame
            self._anchor_time = perf_counter()

    def _tick(self):
        if not self._playing:
            return
        elapsed = perf_counter() - self._anchor_time
        target = self._anchor_frame + int(elapsed * self.speed_combo.currentData() / self.motion.frame_time)
        if target >= self.motion.frame_count:
            if self.loop_check.isChecked():
                target %= self.motion.frame_count
            else:
                target = self.motion.frame_count - 1
                self.pause()
        if target != self.frame:
            self._set_frame(target)

    def closeEvent(self, event):
        self.pause()
        if self._loader and self._loader.isRunning():
            self._close_after_load = True
            self._loader.cancelled.set()
            event.ignore()
            return
        self.plotter.close()
        event.accept()
