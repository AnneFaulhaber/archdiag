#!/usr/bin/env python3
"""
Archdiag MCP server — OpenShift must-gather / oc adm inspect → diagrams.

Tools
-----
* list_namespaces              — discover namespaces in an archive or inspect tree
* analyze_cluster             — parse must-gather cluster metadata (JSON)
* generate_cluster_diagram    — Graphviz cluster architecture PNG
* analyze_namespace           — parse namespace topology model (JSON)
* generate_namespace_diagram  — Console-like namespace topology PNG

Run (stdio, for Cursor / Claude Desktop)::

    python mcp_server.py
    # or
    archdiag-mcp
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP, Image

from openshift_diagram_namespace import generate_namespace_diagram
from openshift_diagram_standard import generate_diagram
from openshift_mg import analyze_must_gather, get_member_list, open_archive
from openshift_ns import analyze_namespace

mcp = FastMCP(
    "archdiag",
    instructions=(
        "Generate OpenShift architecture diagrams from must-gather archives "
        "or oc adm inspect trees. Prefer generate_cluster_diagram for "
        "cluster-wide node/network/storage views, and "
        "generate_namespace_diagram (requires namespace) for Console "
        "Developer–like workload topology. Call list_namespaces first when "
        "the project name is unknown."
    ),
)


def _resolve_output_dir(output_dir: Optional[str]) -> Path:
    if output_dir:
        path = Path(output_dir).expanduser().resolve()
    else:
        path = Path(tempfile.mkdtemp(prefix="archdiag-"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _list_namespaces_in_archive(source: str) -> list[str]:
    handle, _kind = open_archive(source)
    with handle:
        names: set[str] = set()
        for member in get_member_list(handle):
            parts = member.split("/")
            for i, part in enumerate(parts):
                if part == "namespaces" and i + 1 < len(parts):
                    ns = parts[i + 1]
                    if ns and ns not in (".", ".."):
                        names.add(ns)
        return sorted(names)


def _list_namespaces_in_dir(source: str) -> list[str]:
    root = Path(source)
    names: set[str] = set()
    for dirpath, dirnames, _files in os.walk(root):
        parts = Path(dirpath).parts
        for i, part in enumerate(parts):
            if part == "namespaces" and i + 1 < len(parts):
                names.add(parts[i + 1])
        # also catch namespaces/<ns> as current
        if Path(dirpath).name and "namespaces" in Path(dirpath).parts:
            p = Path(dirpath)
            if p.parent.name == "namespaces":
                names.add(p.name)
    return sorted(names)


def _cluster_summary(data: dict) -> dict[str, Any]:
    buckets = data.get("buckets") or {}
    return {
        "cluster_name": data.get("cluster_name"),
        "version": data.get("version"),
        "platform": data.get("platform"),
        "control_plane_topology": data.get("control_plane_topology"),
        "api_url": data.get("api_url"),
        "console_url": data.get("console_url"),
        "node_counts": {k: len(v or []) for k, v in buckets.items()},
        "storage_classes": [
            {"name": sc.get("name"), "default": bool(sc.get("default"))}
            for sc in (data.get("storage_classes") or [])
        ],
        "ingress_domain": (data.get("ingress") or {}).get("domain"),
    }


def _namespace_summary(topo: dict) -> dict[str, Any]:
    return {
        "namespace": topo.get("namespace"),
        "stats": topo.get("stats"),
        "groups": {
            g: [
                {
                    "kind": w.get("kind"),
                    "name": w.get("name"),
                    "ready": w.get("ready"),
                    "desired": w.get("desired"),
                    "pods": len(w.get("pods") or []),
                }
                for w in wls
            ]
            for g, wls in (topo.get("groups") or {}).items()
        },
        "services": [
            {"name": s.get("name"), "targets": s.get("targets"), "type": s.get("type")}
            for s in (topo.get("services") or [])
        ],
        "routes": [
            {"name": r.get("name"), "host": r.get("host"), "service": r.get("service")}
            for r in (topo.get("routes") or [])
        ],
    }


@mcp.tool()
def list_namespaces(source: str) -> str:
    """List namespaces present in a must-gather archive or oc adm inspect directory.

    Args:
        source: Path to .zip / .tar / .tar.gz / .tgz, or an extracted inspect/must-gather directory.
    """
    source = str(Path(source).expanduser())
    if not os.path.exists(source):
        return json.dumps({"error": f"Path not found: {source}"})
    try:
        if os.path.isdir(source):
            namespaces = _list_namespaces_in_dir(source)
        else:
            namespaces = _list_namespaces_in_archive(source)
    except Exception as e:
        return json.dumps({"error": str(e)})
    return json.dumps({"source": source, "namespaces": namespaces, "count": len(namespaces)})


@mcp.tool()
def analyze_cluster(must_gather: str) -> str:
    """Parse a must-gather archive and return cluster metadata as JSON (no diagram).

    Args:
        must_gather: Path to must-gather .zip / .tar / .tar.gz / .tgz.
    """
    must_gather = str(Path(must_gather).expanduser())
    data = analyze_must_gather(must_gather)
    if not data:
        return json.dumps({"error": "Failed to parse must-gather (empty or unsupported)."})
    return json.dumps(_cluster_summary(data), indent=2)


@mcp.tool()
def generate_cluster_diagram(
    must_gather: str,
    output_dir: Optional[str] = None,
    output_prefix: str = "openshift_architecture",
) -> list:
    """Generate a Graphviz cluster architecture diagram (nodes, network, storage, ingress).

    Args:
        must_gather: Path to must-gather .zip / .tar / .tar.gz / .tgz.
        output_dir: Directory for PNG/PDF output (default: temp dir).
        output_prefix: Filename prefix without extension.
    """
    must_gather = str(Path(must_gather).expanduser())
    data = analyze_must_gather(must_gather)
    if not data:
        return ["Failed to parse must-gather (empty or unsupported)."]

    outdir = _resolve_output_dir(output_dir)
    prefix = outdir / output_prefix
    # generate_diagram writes relative to CWD — chdir briefly
    prev = os.getcwd()
    try:
        os.chdir(outdir)
        generate_diagram(data, output_filename=output_prefix)
    finally:
        os.chdir(prev)

    png = Path(f"{prefix}.png")
    pdf = Path(f"{prefix}.pdf")
    summary = {
        **_cluster_summary(data),
        "png": str(png) if png.exists() else None,
        "pdf": str(pdf) if pdf.exists() else None,
    }
    result: list = [json.dumps(summary, indent=2)]
    if png.exists():
        result.append(Image(path=str(png)))
    return result


@mcp.tool()
def analyze_namespace_topology(source: str, namespace: str) -> str:
    """Parse must-gather or oc adm inspect data for one namespace; return topology JSON.

    Args:
        source: Path to must-gather/inspect archive or extracted directory.
        namespace: OpenShift project / Kubernetes namespace (required).
    """
    source = str(Path(source).expanduser())
    topo = analyze_namespace(source, namespace)
    if not topo:
        return json.dumps(
            {
                "error": f"Namespace '{namespace}' not found or failed to parse in {source}",
            }
        )
    return json.dumps(_namespace_summary(topo), indent=2)


@mcp.tool(name="generate_namespace_diagram")
def generate_namespace_diagram_tool(
    source: str,
    namespace: str,
    output_dir: Optional[str] = None,
    output_prefix: Optional[str] = None,
) -> list:
    """Generate a Console Developer–like namespace topology diagram (Service >> Deploy >> RS >> Pod).

    Args:
        source: Path to must-gather/inspect archive or extracted directory.
        namespace: OpenShift project / Kubernetes namespace (required).
        output_dir: Directory for PNG output (default: temp dir).
        output_prefix: Filename prefix without extension (default: openshift_ns_<namespace>).
    """
    source = str(Path(source).expanduser())
    topo = analyze_namespace(source, namespace)
    if not topo:
        return [
            json.dumps(
                {
                    "error": f"Namespace '{namespace}' not found or failed to parse in {source}",
                }
            )
        ]

    outdir = _resolve_output_dir(output_dir)
    prefix_name = output_prefix or f"openshift_ns_{namespace.replace('/', '_')}"
    prev = os.getcwd()
    try:
        os.chdir(outdir)
        generate_namespace_diagram(topo, output_filename=prefix_name)
    finally:
        os.chdir(prev)

    png = outdir / f"{prefix_name}.png"
    summary = {
        **_namespace_summary(topo),
        "png": str(png) if png.exists() else None,
    }
    result: list = [json.dumps(summary, indent=2)]
    if png.exists():
        result.append(Image(path=str(png)))
    return result


def main() -> None:
    # stdio transport — Cursor / Claude Desktop default
    mcp.run()


if __name__ == "__main__":
    main()
