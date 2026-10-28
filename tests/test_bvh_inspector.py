"""BVH transform conventions, format validation and opt-in real-window checks.

Run numerical tests with unittest; set BVH_INSPECTOR_GUI_TEST=1 for Qt/VTK tests.
"""

from concurrent.futures import CancelledError
from dataclasses import replace
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from bvh_inspector.model import BVHParseError, display_basis, load_bvh
from bvh_inspector.filters import hand_detail_indices
from bvh_inspector.camera import body_view_axes, front_camera_pose


FIXTURE = """HIERARCHY
ROOT rig:Hips-01 {
 OFFSET +1.0E1 0 0
 CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation
 JOINT same.name {
  OFFSET 1 0 0
  CHANNELS 3 Xrotation Yrotation Zrotation
  End Site { OFFSET 0 2 0 }
 }
 JOINT same.name {
  OFFSET 0 0 3
  CHANNELS 3 Yrotation Zrotation Xrotation
 }
}
MOTION
Frames: 2
Frame Time: 0.01
2 3 4 90 0 0 0 0 90 0 0 0
3 5 6 20 30 40 10 20 30 40 50 60
"""

FRONT_FIXTURE = """HIERARCHY
ROOT Hips {
 OFFSET 0 0 1
 CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation
 JOINT rig:LeftShoulder { OFFSET 0.3 0 0.6 CHANNELS 0 }
 JOINT rig:RightShoulder { OFFSET -0.3 0 0.6 CHANNELS 0 }
 JOINT LeftUpLeg { OFFSET 0.12 0 0 CHANNELS 0 }
 JOINT RightUpLeg { OFFSET -0.12 0 0 CHANNELS 0 }
 JOINT Head { OFFSET 0 0 1 CHANNELS 0 }
}
MOTION
Frames: 1
Frame Time: 0.01
0 0 0 0 0 0
"""


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "测试 BVH.bvh"

    def load(self, text=FIXTURE):
        self.path.write_text(text, encoding="utf-8")
        return load_bvh(self.path)

    def test_names_branching_duplicate_ids_and_end_sites(self):
        motion = self.load()
        self.assertEqual([j.name for j in motion.joints],
                         ["rig:Hips-01", "same.name", "same.name:EndSite", "same.name"])
        self.assertEqual(motion.joints[0].children, (1, 3))
        self.assertEqual(motion.joints[1].children, (2,))
        self.assertTrue(motion.joints[2].is_end_site)
        self.assertEqual(motion.joints[2].channels, ())
        self.assertNotEqual(motion.node_path(1), motion.node_path(3))
        self.assertEqual(motion.frames.shape, (2, 12))
        self.assertEqual(motion.fps, 100.)
        self.assertEqual(motion.duration, 0.01)

    def test_root_offset_parent_rotation_and_end_site_position(self):
        motion = self.load()
        pose = motion.pose(0)
        np.testing.assert_allclose(pose.world_positions,
                                   [[12, 3, 4], [12, 4, 4], [12, 2, 4], [12, 3, 7]], atol=1e-12)
        np.testing.assert_allclose(pose.local_positions[1], [1, 0, 0])
        np.testing.assert_allclose(pose.local_rotations[2], np.eye(3))
        np.testing.assert_allclose(pose.world_rotations[2], pose.world_rotations[1])

    def test_different_noncommuting_rotation_orders_per_joint(self):
        motion = self.load()
        pose = motion.pose(1)
        root = Rotation.from_euler("ZXY", [20, 30, 40], degrees=True).as_matrix()
        arm = Rotation.from_euler("XYZ", [10, 20, 30], degrees=True).as_matrix()
        branch = Rotation.from_euler("YZX", [40, 50, 60], degrees=True).as_matrix()
        np.testing.assert_allclose(pose.world_rotations[1], root @ arm, atol=1e-12)
        np.testing.assert_allclose(pose.world_rotations[3], root @ branch, atol=1e-12)
        np.testing.assert_allclose(pose.world_positions[1], [13, 5, 6] + root @ [1, 0, 0], atol=1e-12)

    def test_translation_channels_keep_their_declared_order(self):
        text = FIXTURE.replace("Xposition Yposition Zposition Zrotation Xrotation Yrotation",
                               "Zrotation Xposition Yposition Zposition Xrotation Yrotation")
        text = text.replace("2 3 4 90 0 0", "90 2 3 4 0 0")
        np.testing.assert_allclose(self.load(text).pose(0).world_positions[0], [7, 2, 4], atol=1e-12)

    def test_basis_and_scale_preserve_fk_and_do_not_change_source(self):
        motion = self.load()
        original = motion.frames.copy()
        source = motion.pose(1)
        basis = display_basis("y")
        np.testing.assert_allclose(basis @ [0, 1, 0], [0, 0, 1])
        self.assertAlmostEqual(np.linalg.det(basis), 1.)
        converted = source.transformed(basis, 0.01)
        for node in motion.joints[1:]:
            parent = node.parent
            np.testing.assert_allclose(converted.world_positions[node.index],
                                       converted.world_positions[parent] +
                                       converted.world_rotations[parent] @ converted.local_positions[node.index])
            np.testing.assert_allclose(converted.world_rotations[node.index],
                                       converted.world_rotations[parent] @ converted.local_rotations[node.index], atol=1e-12)
        np.testing.assert_array_equal(motion.frames, original)
        np.testing.assert_allclose(display_basis("z"), np.eye(3))
        with self.assertRaises(ValueError):
            display_basis("invalid")

    def test_invalid_motion_is_rejected_with_context(self):
        cases = [
            (FIXTURE.replace("Frames: 2", "Frames: 3"), "实际只有 2"),
            (FIXTURE.replace("Frames: 2", "Frames: 1"), "超出"),
            (FIXTURE.replace("Frames: 2", "Frames: 0"), "Frames"),
            (FIXTURE.replace("Frame Time: 0.01", "Frame Time: nan"), "Frame Time"),
            (FIXTURE.replace("Frame Time: 0.01", "Frame Time: 0"), "Frame Time"),
            (FIXTURE.replace("2 3 4 90 0 0 0 0 90 0 0 0", "1 2"), "第 1 帧"),
            (FIXTURE.replace("2 3 4 90", "nan 3 4 90"), "NaN"),
            (FIXTURE.replace("2 3 4 90", "invalid 3 4 90"), "无效数值"),
            (FIXTURE.replace("Xposition Yposition", "Xposition Xposition"), "重复"),
            (FIXTURE.replace("Xposition", "Xscale"), "不支持"),
            (FIXTURE.replace("+1.0E1", "inf"), "OFFSET"),
            (FIXTURE.replace("MOTION", "BROKEN"), "MOTION"),
        ]
        for text, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(BVHParseError, message):
                self.load(text)

    def test_single_frame_root_only_and_index_bounds(self):
        motion = self.load("HIERARCHY ROOT Only { OFFSET 0 0 0 CHANNELS 1 Xposition }\n"
                           "MOTION\nFrames: 1\nFrame Time: 0.1\n2\n")
        np.testing.assert_array_equal(motion.pose(0).world_positions, [[2, 0, 0]])
        self.assertEqual(motion.duration, 0)
        for frame in (-1, 1):
            with self.assertRaises(IndexError):
                motion.pose(frame)

    def test_cancelled_import(self):
        self.path.write_text(FIXTURE)
        with self.assertRaises(CancelledError):
            load_bvh(self.path, lambda: True)

    def test_anatomical_front_follows_global_yaw_pitch_roll_and_translation(self):
        motion = self.load(FRONT_FIXTURE)
        base = motion.pose(0)
        for angles in ([0, 0, 0], [0, 0, 90], [0, 0, 180], [0, 0, 270], [70, -30, 123], [90, 0, 0]):
            with self.subTest(angles=angles):
                rotation = Rotation.from_euler("xyz", angles, degrees=True).as_matrix()
                pose = replace(base, world_positions=base.world_positions @ rotation.T + [13, -9, 7],
                               world_rotations=rotation @ base.world_rotations)
                right, up, front = body_view_axes(motion, pose)
                np.testing.assert_allclose(right, rotation @ [1, 0, 0], atol=1e-12)
                np.testing.assert_allclose(up, rotation @ [0, 0, 1], atol=1e-12)
                np.testing.assert_allclose(front, rotation @ [0, -1, 0], atol=1e-12)
        # Local root coordinate conventions do not override anatomical landmarks.
        changed = base.world_rotations.copy()
        changed[0] = Rotation.from_euler("y", 90, degrees=True).as_matrix()
        np.testing.assert_allclose(body_view_axes(motion, replace(base, world_rotations=changed))[2], [0, -1, 0])

    def test_front_camera_frames_body_at_different_aspects_and_handles_missing_landmarks(self):
        motion = self.load(FRONT_FIXTURE)
        pose = motion.pose(0)
        original = pose.world_positions.copy()
        for aspect in (0.4, 1., 2.):
            position, target, up = front_camera_pose(motion, pose, list(range(len(motion.joints))), aspect, 30)
            right, _, front = body_view_axes(motion, pose)
            np.testing.assert_allclose((position - target) / np.linalg.norm(position - target), front)
            relative = pose.world_positions - position
            depth = relative @ (-front)
            self.assertTrue(np.all(depth > 0))
            self.assertTrue(np.all(np.abs(relative @ right) <= depth * np.tan(np.deg2rad(15)) * aspect))
            self.assertTrue(np.all(np.abs(relative @ up) <= depth * np.tan(np.deg2rad(15))))
        np.testing.assert_array_equal(pose.world_positions, original)
        # Partial/generic and collapsed skeletons still receive a usable camera.
        generic = self.load(FIXTURE)
        for positions in (generic.pose(0).world_positions, np.zeros((len(generic.joints), 3))):
            axes = np.column_stack(body_view_axes(generic, replace(generic.pose(0), world_positions=positions)))
            self.assertTrue(np.isfinite(axes).all())
            np.testing.assert_allclose(axes.T @ axes, np.eye(3), atol=1e-12)

    def test_hand_detail_filter_preserves_hands_and_unrelated_names(self):
        text = FIXTURE.replace("JOINT same.name", "JOINT rig:LeftHand", 1)
        motion = self.load(text)
        self.assertEqual(hand_detail_indices(motion), {2})  # hand's End Site
        self.assertEqual(hand_detail_indices(self.load(FIXTURE)), set())
        # Fallback catches fingers without a named wrist, but not words like Spring.
        text = FIXTURE.replace("JOINT same.name", "JOINT Spring", 1)
        text = text.replace("JOINT same.name", "JOINT rig:RightIndex1", 1)
        self.assertEqual(hand_detail_indices(self.load(text)), {3})

    def test_project_hand_filter_hides_palm_fingers_and_all_their_end_sites(self):
        path = ROOT / "data/kongtian_BVH/kongfu-normal_001.bvh_Skeleton0.bvh"
        if not path.exists():
            self.skipTest("Kongtian BVH sample unavailable")
        motion = load_bvh(path)
        frozen = motion.frames.copy()
        hidden = hand_detail_indices(motion)
        retained_names = {node.name for node in motion.joints if node.index not in hidden}
        self.assertEqual(len(hidden), 42)
        self.assertTrue({"LeftHand", "RightHand", "LeftForeArm", "RightForeArm"} <= retained_names)
        for node in motion.joints:
            if "FZ" in node.name or "Palm" in node.name:
                self.assertIn(node.index, hidden)
        np.testing.assert_array_equal(motion.frames, frozen)

    def test_project_samples_match_existing_fk_with_root_offset_restored(self):
        # Load the existing parser without importing GMR's robot/SMPL dependencies.
        reference_path = ROOT / "general_motion_retargeting/utils/lafan_vendor/extract.py"
        if not reference_path.is_file():
            self.skipTest("GMR reference loader unavailable in standalone checkout")
        package = types.ModuleType("_inspector_lafan_reference")
        package.__path__ = [str(ROOT / "general_motion_retargeting/utils/lafan_vendor")]
        sys.modules[package.__name__] = package
        spec = importlib.util.spec_from_file_location(
            package.__name__ + ".extract", Path(package.__path__[0]) / "extract.py")
        reference = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reference)
        files = [
            "ubisoft-laforge-animation-dataset/lafan1/lafan1/walk1_subject1.bvh",
            "assets/xsens_bvh_test/251021_04_boxing_120Hz_cm_3DsMax.bvh",
            "data/kongtian_BVH/kongfu-normal_001.bvh_Skeleton0.bvh",
        ]
        checked = 0
        for filename in files:
            path = ROOT / filename
            if not path.exists():
                continue
            with self.subTest(filename=filename):
                motion, old = load_bvh(path), reference.read_bvh(str(path))
                ids = [node.index for node in motion.joints if not node.is_end_site]
                self.assertEqual([motion.joints[i].name for i in ids], old.bones)
                samples = [0, motion.frame_count // 2, motion.frame_count - 1]
                quats, positions = reference.utils.quat_fk(old.quats[samples], old.pos[samples], old.parents)
                for row, frame in enumerate(samples):
                    pose = motion.pose(frame)
                    rotations = Rotation.from_quat(quats[row][:, [1, 2, 3, 0]]).as_matrix()
                    # The legacy loader replaces root OFFSET with motion translation.
                    # This inspector keeps OFFSET + channels, including for the root.
                    expected_positions = positions[row] + np.asarray(motion.joints[0].offset)
                    np.testing.assert_allclose(pose.world_positions[ids], expected_positions, atol=1e-7)
                    np.testing.assert_allclose(pose.world_rotations[ids], rotations, atol=1e-7)
                checked += 1
        if not checked:
            self.skipTest("No project BVH samples available")


@unittest.skipUnless(os.environ.get("BVH_INSPECTOR_GUI_TEST") == "1", "Opt-in Qt/VTK window tests")
class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["QT_API"] = "pyside6"
        from PySide6 import QtCore, QtTest, QtWidgets
        from bvh_inspector.window import BVHInspector
        cls.QtCore, cls.QtTest, cls.QtWidgets = QtCore, QtTest, QtWidgets
        cls.Window = BVHInspector
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "测试.bvh"
        self.path.write_text(FIXTURE, encoding="utf-8")
        self.motion = load_bvh(self.path)
        self.window = self.Window(scale=1, up_axis="z")
        self.window.show()
        self.window.set_motion(self.motion)
        self.QtTest.QTest.qWait(50)

    def tearDown(self):
        self.window.close()
        self.app.processEvents()

    def test_file_dialog_and_background_import(self):
        with patch.object(self.QtWidgets.QFileDialog, "getOpenFileName", return_value=(str(self.path), "")):
            self.window.open_file()
        for _ in range(500):
            self.QtTest.QTest.qWait(10)
            if self.window._loader is None:
                break
        self.assertIsNone(self.window._loader)
        self.assertEqual(self.window.motion.path, self.path)
        self.assertTrue(self.window.open_button.isEnabled())
        self.assertIn("2 帧", self.window.file_info.text())

    def test_tree_table_and_frame_changes_keep_selected_node(self):
        self.window.tree.setCurrentItem(self.window._tree_items[1])
        self.assertEqual(self.window.selected, 1)
        self.assertEqual(self.window.relations.item(1, 0).data(self.QtCore.Qt.ItemDataRole.UserRole), 0)
        self.assertEqual(self.window.relations.item(2, 0).data(self.QtCore.Qt.ItemDataRole.UserRole), 2)
        self.assertIn("12.000000", self.window.relations.item(0, 1).text())
        self.window.reference_combo.setCurrentIndex(1)
        self.assertIn("1.000000", self.window.relations.item(0, 1).text())
        self.window.seek(1)
        self.assertEqual(self.window.selected, 1)
        self.assertEqual(self.window.slider.value(), 1)
        self.assertEqual(self.window.frame_spin.value(), 2)
        self.assertIn("Xrotation: 10", self.window.raw_data.toPlainText())
        self.window._relation_selected(1, 0)
        self.assertEqual(self.window.selected, 0)
        self.assertEqual(self.window.relations.rowCount(), 4)  # current, no parent, both children

    def test_failed_import_preserves_current_motion(self):
        previous = self.window.motion
        with patch.object(self.QtWidgets.QMessageBox, "critical") as error:
            self.window.load_file(self.path.parent / "missing.bvh")
            for _ in range(500):
                self.QtTest.QTest.qWait(10)
                if self.window._loader is None:
                    break
        error.assert_called_once()
        self.assertIs(self.window.motion, previous)
        self.assertIsNone(self.window._loader)
        self.assertTrue(self.window.open_button.isEnabled())
        self.assertTrue(self.window.slider.isEnabled())

    def test_single_frame_root_only_displays_missing_relatives(self):
        self.path.write_text("HIERARCHY ROOT Only { OFFSET 0 0 0 CHANNELS 1 Xposition }\n"
                             "MOTION\nFrames: 1\nFrame Time: 0.1\n2\n")
        self.window.set_motion(load_bvh(self.path))
        self.assertFalse(self.window.play_button.isEnabled())
        self.assertEqual(self.window.relations.rowCount(), 3)
        self.assertEqual(self.window.relations.item(1, 0).text(), "父节点：无")
        self.assertEqual(self.window.relations.item(2, 0).text(), "子节点：无")

    def test_real_mouse_pick_uses_actor_id_and_pauses(self):
        node_id = 3
        position = self.window.display_pose.world_positions[node_id]
        renderer = self.window.plotter.renderer
        renderer.SetWorldPoint(*position, 1)
        renderer.WorldToDisplay()
        x, y, _ = renderer.GetDisplayPoint()
        view = self.window.plotter.interactor
        width, height = self.window.plotter.render_window.GetSize()
        point = self.QtCore.QPoint(round(x * view.width() / width),
                                  round((height - 1 - y) * view.height() / height))
        self.QtTest.QTest.mouseClick(view, self.QtCore.Qt.MouseButton.LeftButton, pos=point)
        self.QtTest.QTest.qWait(50)
        self.assertEqual(self.window.selected, node_id)
        self.assertEqual(self.window.tree.currentItem().data(0, self.QtCore.Qt.ItemDataRole.UserRole), node_id)

    def test_end_sites_labels_basis_scale_and_reload(self):
        self.window.select_joint(2)
        self.window.ends_check.setChecked(False)
        self.assertEqual(self.window.selected, 1)
        self.assertFalse(self.window._joint_actors[2].visibility)
        self.assertTrue(self.window._tree_items[2].isHidden())
        self.window.names_check.setChecked(True)
        self.window.scale_spin.setValue(0.01)
        self.window.up_combo.setCurrentIndex(0)
        np.testing.assert_allclose(self.window.display_pose.world_positions[0], [0.12, -0.04, 0.03])
        self.window.search.setText("same.name")
        self.assertFalse(self.window._tree_items[0].isHidden())
        self.window.set_motion(self.motion)
        self.assertEqual(self.window.search.text(), "")
        self.assertEqual(self.window.selected, 0)

    def test_playback_timing_last_frame_and_loop(self):
        self.window.select_joint(1)
        self.window.toggle_play()
        self.window._anchor_time -= 0.03
        self.window._tick()
        self.assertEqual(self.window.frame, 1)
        self.assertFalse(self.window._playing)
        self.assertEqual(self.window.selected, 1)
        self.window.loop_check.setChecked(True)
        self.window.toggle_play()
        with patch("bvh_inspector.window.perf_counter", return_value=self.window._anchor_time + 0.025):
            self.window._tick()
        self.assertEqual(self.window.frame, 0)
        self.assertTrue(self.window._playing)
        self.window.pause()

    def test_no_fingers_view_keeps_hand_selection_and_hides_all_detail_geometry(self):
        path = ROOT / "data/kongtian_BVH/kongfu-normal_001.bvh_Skeleton0.bvh"
        if not path.exists():
            self.skipTest("Kongtian BVH sample unavailable")
        self.window.close()
        self.window = self.Window(hide_fingers=True)
        self.window.show()
        motion = load_bvh(path)
        self.window.set_motion(motion)
        self.QtTest.QTest.qWait(50)
        hands = {node.name: node.index for node in motion.joints if node.name in ("LeftHand", "RightHand")}
        self.assertEqual(len(self.window._joint_actors), 28)
        for index in self.window._hidden_nodes:
            self.assertNotIn(index, self.window._joint_actors)
            self.assertTrue(self.window._tree_items[index].isHidden())
        self.assertFalse(any(a in self.window._hidden_nodes or b in self.window._hidden_nodes
                             for a, b in self.window._edges))
        for hand in hands.values():
            self.window.select_joint(hand)
            self.assertEqual(self.window.relations.item(2, 0).text(), "子节点：无")
        finger = next(node.index for node in motion.joints if node.name == "FZLeftIndex1")
        self.window.select_joint(finger)
        self.assertEqual(self.window.selected, hands["LeftHand"])
        self.window.ends_check.setChecked(False)
        self.window.ends_check.setChecked(True)
        self.window.names_check.setChecked(True)
        self.window.seek(100)
        self.assertEqual(self.window.selected, hands["LeftHand"])
        self.assertEqual(len(self.window._joint_actors), 28)
        np.testing.assert_allclose(self.window.source_pose.world_positions, motion.pose(100).world_positions)

    def test_import_and_reset_use_front_view_without_changing_pose_or_locking_camera(self):
        self.path.write_text(FRONT_FIXTURE)
        motion = load_bvh(self.path)
        for yaw in (0, 90, 180, 270):
            motion.frames[0, 3] = yaw
            before = motion.frames.copy()
            self.window.set_motion(motion)
            camera = self.window.plotter.camera
            _, expected_up, front = body_view_axes(motion, self.window.display_pose)
            toward_camera = np.array(camera.position) - camera.focal_point
            np.testing.assert_allclose(toward_camera / np.linalg.norm(toward_camera), front, atol=1e-12)
            np.testing.assert_allclose(camera.up, expected_up, atol=1e-12)
            np.testing.assert_array_equal(motion.frames, before)
            camera.azimuth += 30
            manual_position = camera.position
            self.window._refresh_display()
            np.testing.assert_allclose(camera.position, manual_position)
            self.window.reset_camera()
            toward_camera = np.array(camera.position) - camera.focal_point
            np.testing.assert_allclose(toward_camera / np.linalg.norm(toward_camera), front, atol=1e-12)

    def test_initial_front_button_resets_playback_and_camera_but_keeps_selection_and_settings(self):
        self.path.write_text(FIXTURE.replace("Frames: 2", "Frames: 3") + FIXTURE.splitlines()[-1] + "\n")
        motion = load_bvh(self.path)
        frames_before = motion.frames.copy()
        for hide_fingers in (False, True):
            with self.subTest(hide_fingers=hide_fingers):
                self.window.close()
                self.window = self.Window(scale=1, up_axis="z", hide_fingers=hide_fingers)
                self.window.show()
                self.window.set_motion(motion)
                self.QtTest.QTest.qWait(30)
                self.window.select_joint(1)
                self.window.names_check.setChecked(True)
                self.window.ends_check.setChecked(False)
                self.window.reference_combo.setCurrentIndex(1)
                self.window.follow_check.setChecked(True)
                self.window.speed_combo.setCurrentIndex(1)
                self.window.loop_check.setChecked(True)
                self.window.reset_camera()
                camera = self.window.plotter.camera
                initial_camera = np.array([camera.position, camera.focal_point, camera.up])
                self.window.seek(1)
                self.window.toggle_play()
                self.assertTrue(self.window._playing)
                camera.azimuth += 60
                camera.elevation += 20
                camera.position = np.array(camera.position) + [1, 2, 3]
                camera.focal_point = np.array(camera.focal_point) + [1, 2, 3]
                self.QtTest.QTest.mouseClick(self.window.initial_reset_button, self.QtCore.Qt.MouseButton.LeftButton)
                self.assertEqual(self.window.frame, 0)
                self.assertEqual(self.window.slider.value(), 0)
                self.assertEqual(self.window.frame_spin.value(), 1)
                self.assertFalse(self.window._playing)
                self.assertFalse(self.window.timer.isActive())
                self.assertEqual(self.window.selected, 1)
                self.assertTrue(self.window.names_check.isChecked())
                self.assertFalse(self.window.ends_check.isChecked())
                self.assertTrue(self.window.follow_check.isChecked())
                self.assertEqual(self.window.reference_combo.currentIndex(), 1)
                self.assertEqual(self.window.speed_combo.currentIndex(), 1)
                self.assertTrue(self.window.loop_check.isChecked())
                np.testing.assert_allclose([camera.position, camera.focal_point, camera.up], initial_camera, atol=1e-12)
                np.testing.assert_array_equal(motion.frames, frames_before)


if __name__ == "__main__":
    unittest.main()
