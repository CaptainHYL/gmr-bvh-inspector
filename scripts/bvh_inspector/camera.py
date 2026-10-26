"""Anatomical front view, independent of the BVH's world heading/local axes."""

import re

import numpy as np


def _unit(vector):
    length = np.linalg.norm(vector)
    return vector / length if np.isfinite(length) and length > 1e-12 else None


def body_view_axes(motion, pose):
    """Return orthonormal screen-right, screen-up, body-front world vectors.

    Anatomical left appears on the viewer's right in a frontal view. Geometry
    takes precedence over root rotation: BVH joint local bases differ by vendor.
    """
    names = [(node.index, re.sub(r"[^a-z0-9]", "", node.name.rsplit(":", 1)[-1].casefold()))
             for node in motion.joints if not node.is_end_site]
    points = pose.world_positions

    def find(aliases):
        for alias in aliases:
            for index, name in names:
                if name == alias or name.endswith(alias):
                    return index
        return None

    def side(side_name, part):
        short = side_name[0]
        return find((side_name + part, short + part, part + side_name, part + short))

    def pair(parts):
        for part in parts:
            left, right = side("left", part), side("right", part)
            if left is not None and right is not None and _unit(points[left] - points[right]) is not None:
                return left, right
        return None

    shoulders = pair(("shoulder", "collar", "clavicle", "upperarm", "arm"))
    hips = pair(("upleg", "hip", "upperleg", "thigh"))
    root_rotation = pose.world_rotations[0]
    up = None
    if shoulders is not None and hips is not None:
        up = _unit(points[list(shoulders)].mean(axis=0) - points[list(hips)].mean(axis=0))
    if up is None:
        head = find(("head", "headtop", "neck", "chest", "spine2", "spine1"))
        if head is not None:
            up = _unit(points[head] - points[0])
    if up is None:
        up = root_rotation[:, 2].copy()

    lateral = shoulders if shoulders is not None else hips
    front = None
    if lateral is not None:
        leftward = _unit(points[lateral[0]] - points[lateral[1]])
        front = _unit(np.cross(leftward, up))
        if front is None:
            # Degenerate torso: choose an up direction not parallel to the pair.
            up = np.eye(3)[np.argmin(np.abs(leftward))]
            front = _unit(np.cross(leftward, up))
    else:
        # Feet can still identify the front of a partial lower-body skeleton.
        foot_directions = []
        for side_name in ("left", "right"):
            foot = side(side_name, "foot")
            toe = next((index for part in ("toebase", "toe", "toeend")
                        if (index := side(side_name, part)) is not None), None)
            if foot is not None and toe is not None:
                direction = _unit(points[toe] - points[foot])
                if direction is not None:
                    foot_directions.append(direction)
        candidate = np.sum(foot_directions, axis=0) if foot_directions else -root_rotation[:, 1]
        front = _unit(candidate - up * np.dot(candidate, up))
        if front is None:
            candidate = np.eye(3)[np.argmin(np.abs(up))]
            front = _unit(candidate - up * np.dot(candidate, up))
    right = _unit(np.cross(up, front))
    up = np.cross(front, right)
    return right, up, front


def front_camera_pose(motion, pose, visible_indices, aspect, view_angle):
    """Fit the visible skeleton into a perspective camera looking at its front."""
    right, up, front = body_view_axes(motion, pose)
    axes = np.column_stack((right, up, front))
    projected = pose.world_positions[visible_indices] @ axes
    low, high = projected.min(axis=0), projected.max(axis=0)
    center = axes @ ((low + high) / 2)
    half_width, half_height, half_depth = (high - low) / 2
    tangent = np.tan(np.deg2rad(view_angle) / 2)
    # A small margin keeps joint balls/labels off the edge. Depth is included so
    # forward-reaching limbs fit too, rather than only fitting the torso plane.
    distance = 1.18 * (max(half_height / tangent, half_width / (tangent * max(aspect, 0.1)), 0.05)
                       + half_depth)
    return center + front * distance, center, up
