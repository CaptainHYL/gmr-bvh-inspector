"""BVH hierarchy parsing and forward kinematics in the original coordinate basis.

Local transforms are OFFSET followed by the channel transforms in file order.
No unit conversion, ground alignment, or root motion removal happens here.
"""

from dataclasses import dataclass
from pathlib import Path
import re
from concurrent.futures import CancelledError
from typing import Callable, Optional

import numpy as np


class BVHParseError(ValueError):
    """A malformed or unsupported BVH, with a useful location in the message."""


@dataclass(frozen=True)
class Joint:
    index: int
    name: str
    parent: int
    children: tuple
    offset: tuple
    channels: tuple
    channel_start: int
    is_end_site: bool = False


@dataclass
class Pose:
    local_positions: np.ndarray
    local_rotations: np.ndarray
    world_positions: np.ndarray
    world_rotations: np.ndarray

    def transformed(self, basis: np.ndarray, scale: float) -> "Pose":
        """Change both world and joint coordinate bases; express positions in m."""
        return Pose(
            self.local_positions @ basis.T * scale,
            basis @ self.local_rotations @ basis.T,
            self.world_positions @ basis.T * scale,
            basis @ self.world_rotations @ basis.T,
        )


@dataclass
class BVHMotion:
    path: Path
    joints: tuple
    frames: np.ndarray
    frame_time: float

    @property
    def frame_count(self):
        return self.frames.shape[0]

    @property
    def fps(self):
        return 1.0 / self.frame_time

    @property
    def duration(self):
        """Timestamp of the last frame (the first frame has timestamp zero)."""
        return (self.frame_count - 1) * self.frame_time

    def node_path(self, index):
        parts = []
        while index >= 0:
            node = self.joints[index]
            parts.append(f"{node.name} [#{index}]")
            index = node.parent
        return "/" + "/".join(reversed(parts))

    def pose(self, frame: int) -> Pose:
        if not 0 <= frame < self.frame_count:
            raise IndexError(f"Frame {frame} is outside [0, {self.frame_count}).")
        count = len(self.joints)
        local_p = np.empty((count, 3))
        local_r = np.empty((count, 3, 3))
        world_p = np.empty_like(local_p)
        world_r = np.empty_like(local_r)
        for node in self.joints:
            index = node.index
            p = np.array(node.offset, dtype=float)
            rotation = np.eye(3)
            for channel, value in zip(node.channels, self.channel_values(frame, index)):
                axis = "XYZ".index(channel[0])
                if channel.endswith("position"):
                    # Preserve channel order, including interleaved translations.
                    p += rotation[:, axis] * value
                else:
                    angle = np.deg2rad(value)
                    c, s = np.cos(angle), np.sin(angle)
                    step = np.eye(3)
                    a, b = (axis + 1) % 3, (axis + 2) % 3
                    step[a, a] = step[b, b] = c
                    step[a, b], step[b, a] = -s, s
                    rotation = rotation @ step
            local_p[index], local_r[index] = p, rotation
            if node.parent < 0:
                world_p[index], world_r[index] = p, rotation
            else:
                world_p[index] = world_p[node.parent] + world_r[node.parent] @ p
                world_r[index] = world_r[node.parent] @ rotation
        return Pose(local_p, local_r, world_p, world_r)

    def channel_values(self, frame, index):
        node = self.joints[index]
        return self.frames[frame, node.channel_start:node.channel_start + len(node.channels)]


def display_basis(up_axis: str) -> np.ndarray:
    """Display in a right-handed Z-up basis without modifying the source data."""
    if up_axis.lower() == "y":
        return np.array([[1., 0., 0.], [0., 0., -1.], [0., 1., 0.]])
    if up_axis.lower() == "z":
        return np.eye(3)
    raise ValueError("Source up axis must be 'y' or 'z'.")


class _HierarchyParser:
    def __init__(self, text):
        self.tokens = re.findall(r"[{}]|[^\s{}]+", text)
        self.cursor = 0
        self.nodes = []
        self.channel_count = 0

    def pop(self):
        if self.cursor >= len(self.tokens):
            raise BVHParseError("HIERARCHY 意外结束；请检查花括号和节点定义。")
        token = self.tokens[self.cursor]
        self.cursor += 1
        return token

    def expect(self, token):
        actual = self.pop()
        if actual != token:
            raise BVHParseError(f"HIERARCHY: 预期 {token!r}，实际为 {actual!r}。")

    def node(self, parent=-1, is_end_site=False):
        if is_end_site:
            name = self.nodes[parent].name + ":EndSite"
        else:
            # Preserve punctuation, namespaces and even exporter names with spaces.
            parts = [self.pop()]
            while self.cursor < len(self.tokens) and self.tokens[self.cursor] != "{":
                parts.append(self.pop())
            name = " ".join(parts)
            if parts[0] in ("{", "}"):
                raise BVHParseError("HIERARCHY: 节点名称缺失。")
        self.expect("{")
        self.expect("OFFSET")
        try:
            offset = tuple(float(self.pop()) for _ in range(3))
        except ValueError as exc:
            raise BVHParseError(f"{name}: OFFSET 必须包含三个有效数值。") from exc
        if not np.isfinite(offset).all():
            raise BVHParseError(f"{name}: OFFSET 不能含有 NaN 或无穷大。")
        channels = ()
        start = self.channel_count
        if not is_end_site:
            self.expect("CHANNELS")
            try:
                count = int(self.pop())
            except ValueError as exc:
                raise BVHParseError(f"{name}: CHANNELS 数量必须为整数。") from exc
            if not 0 <= count <= 6:
                raise BVHParseError(f"{name}: 仅支持 0–6 个平移/旋转通道，实际为 {count}。")
            channels = tuple(self.pop() for _ in range(count))
            valid = {axis + kind for axis in "XYZ" for kind in ("position", "rotation")}
            if len(set(channels)) != count or any(c not in valid for c in channels):
                raise BVHParseError(f"{name}: 存在重复或不支持的通道：{channels}。")
            self.channel_count += count
        index = len(self.nodes)
        self.nodes.append(Joint(index, name, parent, (), offset, channels, start, is_end_site))
        children = []
        while True:
            token = self.pop()
            if token == "}":
                break
            if token == "JOINT" and not is_end_site:
                children.append(self.node(index))
            elif token == "End" and not is_end_site:
                self.expect("Site")
                children.append(self.node(index, True))
            else:
                raise BVHParseError(f"{name}: 预期 JOINT、End Site 或 '}}'，实际为 {token!r}。")
        self.nodes[index] = Joint(index, name, parent, tuple(children), offset, channels, start, is_end_site)
        return index

    def parse(self):
        self.expect("HIERARCHY")
        self.expect("ROOT")
        self.node()
        if self.cursor != len(self.tokens):
            raise BVHParseError("HIERARCHY: 根节点结束后存在多余内容；仅支持单个 ROOT。")
        if not self.channel_count:
            raise BVHParseError("BVH 至少需要一个运动通道。")
        return tuple(self.nodes)


def load_bvh(path, cancelled: Optional[Callable[[], bool]] = None) -> BVHMotion:
    """Read one motion row per frame, with strict channel/frame validation."""
    path = Path(path).expanduser().resolve()

    def check_cancelled():
        if cancelled and cancelled():
            raise CancelledError()

    with path.open("r", encoding="utf-8-sig") as stream:
        header = []
        for line in stream:
            check_cancelled()
            if line.strip() == "MOTION":
                break
            header.append(line)
        else:
            raise BVHParseError("找不到 MOTION 段。")
        parser = _HierarchyParser("".join(header))
        joints = parser.parse()

        def nonempty():
            for row in stream:
                check_cancelled()
                if row.strip():
                    return row.strip()
            raise BVHParseError("MOTION 段意外结束。")

        frame_match = re.fullmatch(r"Frames\s*:\s*(\d+)", nonempty())
        if not frame_match or int(frame_match[1]) < 1:
            raise BVHParseError("Frames 必须为正整数。")
        frame_count = int(frame_match[1])
        time_match = re.fullmatch(r"Frame\s+Time\s*:\s*(\S+)", nonempty())
        try:
            frame_time = float(time_match[1]) if time_match else 0.0
        except ValueError as exc:
            raise BVHParseError("Frame Time 必须为有效数值。") from exc
        if not np.isfinite(frame_time) or frame_time <= 0:
            raise BVHParseError("Frame Time 必须为有限的正数。")
        frames = np.empty((frame_count, parser.channel_count), dtype=np.float64)
        row_index = 0
        for line in stream:
            check_cancelled()
            if not line.strip():
                continue
            if row_index >= frame_count:
                raise BVHParseError(f"运动数据超出声明的 {frame_count} 帧。")
            parts = line.split()
            if len(parts) != parser.channel_count:
                raise BVHParseError(
                    f"第 {row_index + 1} 帧：应有 {parser.channel_count} 个通道值，实际为 {len(parts)}。"
                )
            try:
                frames[row_index] = parts
            except ValueError as exc:
                raise BVHParseError(f"第 {row_index + 1} 帧包含无效数值。") from exc
            if not np.isfinite(frames[row_index]).all():
                raise BVHParseError(f"第 {row_index + 1} 帧包含 NaN 或无穷大。")
            row_index += 1
        if row_index != frame_count:
            raise BVHParseError(f"声明 {frame_count} 帧，实际只有 {row_index} 帧。")
    check_cancelled()
    return BVHMotion(path, joints, frames, frame_time)
