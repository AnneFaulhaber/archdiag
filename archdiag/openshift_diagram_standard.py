#!/usr/bin/env python3
"""
OpenShift must-gather → Graphviz architecture diagram.

Parsing: openshift_mg.py
Brand tokens: rh_brand.py

One equal-width canvas (shared PANEL_WIDTH) with three stacked sections:
  1. Cluster information
  2. Network · Storage · Ingress  (gray / interaction-blue scheme)
  3. Cluster nodes                (shared teal palette, exclusive buckets)

HTML labels avoid empty cells, nested <FONT>, and POINT-SIZE < 8
(fixes Pango-CRITICAL from cairo).
"""

from __future__ import annotations

import html
import sys
from typing import Dict, List, Optional, Tuple

from graphviz import Digraph

from . import rh_brand as rh
from .openshift_mg import analyze_must_gather


def _esc(value) -> str:
    return html.escape(str(value if value is not None else "N/A"))


def _join(values: List[str], fallback: str = "N/A") -> str:
    cleaned = [v for v in values if v]
    return ", ".join(cleaned) if cleaned else fallback


def _chunk(items: list, size: int) -> List[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _nbsp() -> str:
    return "&#160;"


def _section_title(text: str, fg: str, bg: str) -> str:
    return (
        f'<TR><TD WIDTH="{rh.PANEL_WIDTH}" BGCOLOR="{bg}" CELLPADDING="8" ALIGN="LEFT" BORDER="0">'
        f'<FONT POINT-SIZE="12" COLOR="{fg}"><B>{_esc(text)}</B></FONT></TD></TR>'
    )


def _kv_rows(rows: List[Tuple[str, str]]) -> str:
    parts = []
    for key, val in rows:
        parts.append(
            "<TR>"
            f'<TD ALIGN="LEFT" WIDTH="120">'
            f'<FONT POINT-SIZE="11" COLOR="{rh.TEXT_MUTED}"><B>{_esc(key)}</B></FONT></TD>'
            f'<TD ALIGN="LEFT">'
            f'<FONT POINT-SIZE="11" COLOR="{rh.TEXT}">{_esc(val)}</FONT></TD>'
            "</TR>"
        )
    return "".join(parts)


def _info_block(data: dict) -> str:
    cluster_name = data.get("cluster_name") or "Unknown Cluster"
    rows = [
        ("Version", f"{data.get('version', 'N/A')} ({data.get('version_state', 'N/A')})"),
        ("Channel", data.get("channel", "N/A")),
        ("Cluster ID", data.get("cluster_id", "N/A")),
        ("Platform", data.get("platform", "N/A")),
        (
            "Topology",
            f"CP: {data.get('control_plane_topology', 'N/A')} · "
            f"Infra: {data.get('infrastructure_topology', 'N/A')}",
        ),
        ("API", data.get("api_url", "N/A")),
    ]
    if data.get("console_url") and data["console_url"] != "N/A":
        rows.append(("Console", data["console_url"]))

    # Accent (12) + body must equal INNER_WIDTH so the box matches siblings.
    body_w = rh.INNER_WIDTH - 12
    return (
        f'<TABLE BORDER="1" COLOR="{rh.SURFACE_BORDER}" CELLBORDER="0" '
        f'CELLSPACING="0" CELLPADDING="0" WIDTH="{rh.INNER_WIDTH}" '
        f'BGCOLOR="{rh.WHITE}">'
        "<TR>"
        f'<TD WIDTH="12" BGCOLOR="{rh.RED_50}" ALIGN="CENTER">'
        f'<FONT POINT-SIZE="10" COLOR="{rh.RED_50}">{_nbsp()}</FONT></TD>'
        f'<TD WIDTH="{body_w}" CELLPADDING="10">'
        f'<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="3" CELLPADDING="2" WIDTH="{body_w - 20}">'
        "<TR><TD COLSPAN=\"2\" ALIGN=\"LEFT\">"
        f'<FONT POINT-SIZE="16" COLOR="{rh.TEXT}">'
        f"<B>OpenShift Cluster: {_esc(cluster_name)}</B></FONT></TD></TR>"
        f"{_kv_rows(rows)}"
        "</TABLE></TD></TR></TABLE>"
    )


def _config_column(title: str, rows: List[Tuple[str, str]], style: dict, col_w: int) -> str:
    body = []
    for k, v in rows:
        body.append(
            f'<TR><TD ALIGN="LEFT" BGCOLOR="{style["body_bg"]}" BORDER="1" '
            f'COLOR="{style["border"]}" CELLPADDING="5" WIDTH="{col_w - 8}">'
            f'<FONT POINT-SIZE="10" COLOR="{rh.TEXT}">'
            f"<B>{_esc(k)}</B>  {_esc(v)}</FONT></TD></TR>"
        )
    return (
        f'<TD WIDTH="{col_w}" VALIGN="TOP" CELLPADDING="0">'
        f'<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0" CELLPADDING="0" WIDTH="{col_w}">'
        f'<TR><TD WIDTH="{col_w}" BGCOLOR="{style["header_bg"]}" BORDER="1" '
        f'COLOR="{style["border"]}" CELLPADDING="6" ALIGN="LEFT">'
        f'<FONT POINT-SIZE="12" COLOR="{style["header_fg"]}"><B>{_esc(title)}</B></FONT>'
        f"</TD></TR>{''.join(body)}</TABLE></TD>"
    )


def _config_block(data: dict) -> str:
    net = data.get("network") or {}
    storage_classes = data.get("storage_classes") or []
    ingress = data.get("ingress") or {}

    if storage_classes:
        sc_rows = [
            (
                f"{sc.get('name', 'N/A')}{' (default)' if sc.get('default') else ''}",
                sc.get("provisioner", "N/A"),
            )
            for sc in storage_classes
        ]
    else:
        sc_rows = [("Status", "None found")]

    # Pad so 3 * CONFIG_COL_WIDTH == INNER_WIDTH (absorb remainder in last col).
    c0 = rh.CONFIG_COL_WIDTH
    c1 = rh.CONFIG_COL_WIDTH
    c2 = rh.INNER_WIDTH - c0 - c1

    return (
        f'<TABLE BORDER="1" COLOR="{rh.CONFIG_STYLE["panel_border"]}" CELLBORDER="0" '
        f'CELLSPACING="0" CELLPADDING="0" WIDTH="{rh.INNER_WIDTH}" '
        f'BGCOLOR="{rh.CONFIG_STYLE["panel_fill"]}">'
        "<TR>"
        + _config_column(
            "Network",
            [
                ("Type", net.get("networkType", "N/A")),
                ("Cluster CIDR", _join(net.get("clusterNetwork") or [])),
                ("Service CIDR", _join(net.get("serviceNetwork") or [])),
                ("Machine CIDR", _join(net.get("machineNetwork") or [])),
                ("MTU", str(net.get("mtu", "N/A"))),
            ],
            rh.CONFIG_STYLE["network"],
            c0,
        )
        + _config_column(
            "Storage Classes",
            sc_rows,
            rh.CONFIG_STYLE["storage"],
            c1,
        )
        + _config_column(
            "Ingress",
            [
                ("Domain", ingress.get("domain", "N/A")),
                ("Apps domain", ingress.get("appsDomain", "N/A")),
            ],
            rh.CONFIG_STYLE["ingress"],
            c2,
        )
        + "</TR></TABLE>"
    )


def _node_cell(node: dict, fill: str, border: str) -> str:
    secondary = node.get("secondary_roles") or []
    secondary_str = ", ".join(secondary[:3])
    if len(secondary) > 3:
        secondary_str += ", ..."
    primary = _esc(node.get("primary_role", "node"))
    roles = f"{primary} + {_esc(secondary_str)}" if secondary_str else primary
    ready = node.get("ready", "Unknown")
    ready_color = rh.status_color(ready)
    name = _esc(node.get("short_name") or node.get("name"))
    w = rh.NODE_CELL_WIDTH

    return (
        f'<TD BORDER="1" COLOR="{border}" BGCOLOR="{fill}" CELLPADDING="6" '
        f'WIDTH="{w}" VALIGN="TOP">'
        f'<FONT POINT-SIZE="12" COLOR="{rh.TEXT}"><B>{name}</B></FONT><BR/>'
        f'<FONT POINT-SIZE="9" COLOR="{rh.TEXT_MUTED}">{roles}</FONT><BR/>'
        f'<FONT POINT-SIZE="9" COLOR="{rh.TEXT}">'
        f'CPU {_esc(node.get("cpu"))} · Mem {_esc(node.get("memory"))}</FONT><BR/>'
        f'<FONT POINT-SIZE="9" COLOR="{rh.TEXT_MUTED}">'
        f'IP {_esc(node.get("internal_ip"))}</FONT><BR/>'
        f'<FONT POINT-SIZE="9" COLOR="{ready_color}"><B>{_esc(ready)}</B></FONT>'
        f"</TD>"
    )


def _pad_cell() -> str:
    return (
        f'<TD BORDER="0" WIDTH="{rh.NODE_CELL_WIDTH}" CELLPADDING="6" BGCOLOR="{rh.TEAL_10}">'
        f'<FONT POINT-SIZE="9" COLOR="{rh.TEAL_10}">{_nbsp()}</FONT></TD>'
    )


def _role_block(bucket: str, nodes: List[dict]) -> str:
    style = rh.BUCKET_STYLE[bucket]
    cols = rh.NODE_COLS
    rows = [
        f'<TR><TD COLSPAN="{cols}" BGCOLOR="{style["header_bg"]}" CELLPADDING="6" ALIGN="LEFT">'
        f'<FONT POINT-SIZE="13" COLOR="{style["header_fg"]}">'
        f'<B>{_esc(style["title"])}</B> ({len(nodes)})</FONT></TD></TR>'
    ]
    for row in _chunk(nodes, cols):
        cells = [_node_cell(n, style["node_fill"], style["node_border"]) for n in row]
        while len(cells) < cols:
            cells.append(_pad_cell())
        rows.append(f"<TR>{''.join(cells)}</TR>")
    return "".join(rows)


def _nodes_block(
    buckets: Dict[str, List[dict]],
    all_nodes: Optional[List[dict]] = None,
) -> str:
    from openshift_mg import fleet_stats

    blocks = []
    if all_nodes:
        stats = fleet_stats(all_nodes)
        status_bits = [f"{stats['ready']} Ready"]
        if stats["not_ready"]:
            status_bits.append(f"{stats['not_ready']} NotReady")
        if stats["unknown"]:
            status_bits.append(f"{stats['unknown']} Unknown")
        blocks.append(
            f'<TR><TD COLSPAN="{rh.NODE_COLS}" BGCOLOR="{rh.WHITE}" CELLPADDING="6" ALIGN="LEFT">'
            f'<FONT POINT-SIZE="10" COLOR="{rh.TEXT}">'
            f"Fleet: {stats['total']} nodes · {' · '.join(status_bits)}"
            f"</FONT></TD></TR>"
        )

    for bucket in ("control-plane", "infra", "worker", "other"):
        nodes = buckets.get(bucket) or []
        if nodes:
            blocks.append(_role_block(bucket, nodes))
    if len(blocks) <= (1 if all_nodes else 0):
        blocks.append(
            f'<TR><TD COLSPAN="{rh.NODE_COLS}">'
            f'<FONT POINT-SIZE="12" COLOR="{rh.TEXT}"><B>No nodes found</B></FONT></TD></TR>'
        )
    return (
        f'<TABLE BORDER="1" COLOR="{rh.TEAL_50}" CELLBORDER="0" CELLSPACING="0" '
        f'CELLPADDING="0" WIDTH="{rh.INNER_WIDTH}" BGCOLOR="{rh.TEAL_10}">'
        + "".join(blocks)
        + "</TABLE>"
    )


def _panel(
    title: str,
    title_fg: str,
    title_bg: str,
    body_html: str,
    border: str,
    fill: str,
) -> str:
    """One equal-width section box (title bar + body)."""
    return (
        f'<<TABLE WIDTH="{rh.PANEL_WIDTH}" BORDER="1" COLOR="{border}" '
        f'BGCOLOR="{fill}" CELLBORDER="0" CELLSPACING="0" CELLPADDING="0">'
        f'{_section_title(title, title_fg, title_bg)}'
        f'<TR><TD WIDTH="{rh.PANEL_WIDTH}" CELLPADDING="12" ALIGN="LEFT" BGCOLOR="{fill}">'
        f"{body_html}</TD></TR>"
        f"</TABLE>>"
    )


def generate_diagram(
    data: dict,
    output_filename: str = "openshift_architecture",
) -> None:
    """Three equal-width section boxes with visible gaps between them."""
    from openshift_mg import sort_buckets_health_first

    buckets = sort_buckets_health_first(
        {k: list(v) for k, v in (data.get("buckets") or {}).items()}
    )

    dot = Digraph(
        comment="OpenShift Cluster Architecture",
        graph_attr={
            "rankdir": "TB",
            "bgcolor": rh.BG,
            "fontname": rh.FONT_TEXT,
            "fontsize": "12",
            "pad": "0.35",
            "margin": "0.2",
            "ranksep": "0.55",
            "nodesep": "0.4",
            "newrank": "true",
        },
    )
    dot.attr("node", shape="plaintext", fontname=rh.FONT_TEXT, fontcolor=rh.TEXT)
    dot.attr("edge", style="invis", weight="100")

    info = _panel(
        "Cluster information",
        rh.TEXT_MUTED,
        rh.GRAY_10,
        _info_block(data),
        rh.GRAY_30,
        rh.WHITE,
    )
    config = _panel(
        "Network · Storage · Ingress",
        rh.TEXT_MUTED,
        rh.GRAY_20,
        _config_block(data),
        rh.CONFIG_STYLE["panel_border"],
        rh.CONFIG_STYLE["panel_fill"],
    )
    nodes = _panel(
        "Cluster nodes",
        rh.TEAL_70,
        rh.TEAL_20,
        _nodes_block(buckets, all_nodes=data.get("nodes") or []),
        rh.TEAL_50,
        rh.TEAL_10,
    )

    dot.node("panel_info", info, width=rh.PANEL_WIDTH_IN)
    dot.node("panel_config", config, width=rh.PANEL_WIDTH_IN)
    dot.node("panel_nodes", nodes, width=rh.PANEL_WIDTH_IN)

    dot.edge("panel_info", "panel_config")
    dot.edge("panel_config", "panel_nodes")

    print(f"\nRendering diagram to {output_filename}.png and {output_filename}.pdf...")
    dot.render(output_filename, format="png", cleanup=True)
    dot.render(output_filename, format="pdf", cleanup=True)
    print("Done. Check your current directory for the generated files.")


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate an OpenShift architecture diagram from a must-gather archive."
    )
    parser.add_argument(
        "must_gather",
        nargs="?",
        help="Path to must-gather .zip / .tar / .tar.gz / .tgz",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="openshift_architecture",
        help="Output filename prefix (default: openshift_architecture)",
    )
    args = parser.parse_args(argv if argv is not None else None)

    must_gather_file = args.must_gather
    if not must_gather_file:
        must_gather_file = input(
            "Please enter the path to the OpenShift Must Gather ZIP or TAR.GZ file: "
        ).strip()

    if not must_gather_file:
        print("No file path provided. Exiting.")
        return 1

    data = analyze_must_gather(must_gather_file)
    if not data:
        return 1
    generate_diagram(data, output_filename=args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
