"""Display-only hand-detail filtering; source BVH nodes/channels stay intact."""

import re


def hand_detail_indices(motion):
    """Hide everything beyond a hand/wrist, with a finger-name fallback.

    Hand/wrist anchors themselves remain visible. Following their entire subtree
    also catches palms, exporter-specific finger names and unnamed End Sites.
    """
    hidden = set()
    anchors = set()
    for node in motion.joints:
        name = re.sub(r"[^a-z0-9]", "", node.name.rsplit(":", 1)[-1].casefold())
        hand = re.search(r"(?:(?:left|right|l|r)(?:hand|wrist)|(?:hand|wrist)(?:left|right|l|r))$", name)
        is_anchor = name in ("hand", "wrist") or bool(hand)
        detail = re.search(r"(?:^|left|right|hand|l|r)(?:thumb|index|middle|ring|pinky|little|finger|palm)"
                           r"\d*(?:end(?:site)?)?$", name)
        if node.parent in hidden or node.parent in anchors:
            hidden.add(node.index)
        elif is_anchor:
            anchors.add(node.index)
        elif detail and node.parent >= 0:
            hidden.add(node.index)
    return frozenset(hidden)
