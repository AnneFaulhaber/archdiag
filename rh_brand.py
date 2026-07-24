"""
Red Hat brand tokens for architecture diagrams.

Source: https://www.redhat.com/en/about/brand/standards/color
         https://www.redhat.com/en/about/brand/standards/typography

Diagram colour strategy:
- Cluster nodes share ONE secondary family (teal), differentiated by shade.
- Network / Storage / Ingress share a separate gray + interaction-blue scheme.
- Red Hat red (#ee0000) is a brand accent only — never used for errors.
- Status uses the information palette (success-green / danger-orange).
"""

from __future__ import annotations

# --- Core palette ---
RED_50 = "#ee0000"  # Red Hat red (accent only)
RED_10 = "#fce3e3"

WHITE = "#ffffff"
BLACK = "#000000"
GRAY_10 = "#f2f2f2"
GRAY_20 = "#e0e0e0"
GRAY_30 = "#c7c7c7"
GRAY_40 = "#a3a3a3"
GRAY_50 = "#707070"
GRAY_60 = "#4d4d4d"
GRAY_70 = "#383838"
GRAY_90 = "#1f1f1f"
GRAY_95 = "#151515"

# --- Secondary: teal (shared node palette) ---
TEAL_10 = "#daf2f2"
TEAL_20 = "#b9e5e5"
TEAL_30 = "#9ad8d8"
TEAL_40 = "#63bdbd"
TEAL_50 = "#37a3a3"
TEAL_60 = "#147878"
TEAL_70 = "#004d4d"

# --- Information palette ---
SUCCESS_GREEN_60 = "#3d7317"
DANGER_ORANGE_60 = "#b1380b"
INTERACTION_BLUE_10 = "#e0f0ff"
INTERACTION_BLUE_20 = "#b9dafc"
INTERACTION_BLUE_40 = "#4394e5"
INTERACTION_BLUE_60 = "#004d99"

# --- Typography ---
# Prefer Red Hat fonts when installed; Helvetica is a safe Graphviz fallback.
FONT_DISPLAY = "Helvetica"
FONT_TEXT = "Helvetica"
FONT_FACE = FONT_TEXT
FONT_FACE_DISPLAY = FONT_DISPLAY

# Canvas
BG = WHITE
SURFACE = GRAY_10
SURFACE_BORDER = GRAY_30
TEXT = GRAY_95
TEXT_MUTED = GRAY_60

# Fixed canvas width (points). Every section’s columns sum to this value.
PANEL_WIDTH = 880
PANEL_WIDTH_IN = f"{PANEL_WIDTH / 72.0:.3f}"
NODE_COLS = 4
# Content area after outer cell padding (10+10).
INNER_WIDTH = PANEL_WIDTH - 20
NODE_CELL_WIDTH = INNER_WIDTH // NODE_COLS  # exact fit: 4 * cell = INNER
CONFIG_COLS = 3
CONFIG_COL_WIDTH = INNER_WIDTH // CONFIG_COLS

# Node groups: same teal family, darker = higher privilege
BUCKET_STYLE = {
    "control-plane": {
        "title": "Control Plane",
        "cluster_fill": TEAL_10,
        "cluster_border": TEAL_50,
        "node_fill": TEAL_20,
        "node_border": TEAL_60,
        "header_bg": TEAL_50,
        "header_fg": WHITE,
    },
    "infra": {
        "title": "Infra Nodes",
        "cluster_fill": TEAL_10,
        "cluster_border": TEAL_40,
        "node_fill": TEAL_10,
        "node_border": TEAL_50,
        "header_bg": TEAL_40,
        "header_fg": TEAL_70,
    },
    "worker": {
        "title": "Worker Nodes",
        "cluster_fill": TEAL_10,
        "cluster_border": TEAL_30,
        "node_fill": WHITE,
        "node_border": TEAL_40,
        "header_bg": TEAL_30,
        "header_fg": TEAL_70,
    },
    "other": {
        "title": "Other Nodes",
        "cluster_fill": TEAL_10,
        "cluster_border": TEAL_20,
        "node_fill": WHITE,
        "node_border": TEAL_30,
        "header_bg": TEAL_20,
        "header_fg": TEAL_70,
    },
}

# Config strip: separate gray / interaction-blue scheme
CONFIG_STYLE = {
    "panel_border": GRAY_40,
    "panel_fill": GRAY_10,
    "network": {
        "header_bg": GRAY_30,
        "header_fg": GRAY_90,
        "border": GRAY_40,
        "body_bg": WHITE,
    },
    "storage": {
        "header_bg": GRAY_40,
        "header_fg": WHITE,
        "border": GRAY_50,
        "body_bg": WHITE,
    },
    "ingress": {
        "header_bg": INTERACTION_BLUE_40,
        "header_fg": WHITE,
        "border": INTERACTION_BLUE_60,
        "body_bg": INTERACTION_BLUE_10,
    },
}


def status_color(ready: str) -> str:
    """Information-palette status color. Never use brand red for failure."""
    if ready == "Ready":
        return SUCCESS_GREEN_60
    if ready == "NotReady":
        return DANGER_ORANGE_60
    return GRAY_50
