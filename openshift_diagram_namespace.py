#!/usr/bin/env python3
"""
DRAFT: Namespace topology diagram (Console Developer–like).

Renders per-workload graphs inspired by diagrams.mingrammer.com:
  - "Exposed Pod with 3 Replicas on Kubernetes"  → Deployment / DC / DS
  - "Stateful Architecture on Kubernetes"        → StatefulSet + PVC/PV/SC

Input: must-gather archive OR `oc adm inspect <namespace>` dir/archive,
plus a required --namespace.

CLI (draft):
  python openshift_diagram_namespace.py ./inspect.local.* -n myapp
  python openshift_diagram_namespace.py must-gather.tar.gz -n openshift-monitoring -o mon-topo
"""

from __future__ import annotations

import argparse
import os
from typing import Dict, List, Optional

from diagrams import Cluster, Diagram
from diagrams.k8s.clusterconfig import HPA
from diagrams.k8s.compute import DaemonSet, Deployment, Pod, ReplicaSet, StatefulSet
from diagrams.k8s.network import Ingress, Service
from diagrams.k8s.storage import PV, PVC, StorageClass

import rh_brand as rh
from openshift_ns import analyze_namespace

# OpenShift Route has no first-class diagrams.k8s icon; reuse Ingress styling
# with an explicit "Route" label (Console shows Route as the public URL edge).
try:
    from diagrams.onprem.network import Envoy as RouteIcon  # noqa: F401 — unused fallback
except Exception:  # pragma: no cover
    RouteIcon = None


MAX_PODS_DRAWN = 6  # Console collapses large replica sets visually; draft cap
# Soft wrap width for resource names (chars). Hyphenated k8s names wrap at `-`.
LABEL_WIDTH = 18

# Status strings shown in red (Graphviz HTML <FONT COLOR=...>).
_PROBLEM_PHASES = frozenset(
    {
        "Pending",
        "Failed",
        "Unknown",
        "Error",
        "CrashLoopBackOff",
        "ImagePullBackOff",
        "ErrImagePull",
        "OOMKilled",
        "NotReady",
        "?",
    }
)
_STATUS_RED = rh.RED_50  # #ee0000


def _wrap_name(name: str, width: int = LABEL_WIDTH) -> str:
    """Break long resource names onto multiple lines so labels do not collide."""
    name = (name or "").strip() or "unnamed"
    if len(name) <= width:
        return name

    parts = name.split("-")
    if len(parts) == 1:
        # No hyphens: hard-wrap
        return "\n".join(name[i : i + width] for i in range(0, len(name), width))

    lines: List[str] = []
    current = parts[0]
    for part in parts[1:]:
        candidate = f"{current}-{part}"
        if len(candidate) <= width:
            current = candidate
        else:
            lines.append(current)
            current = part
    lines.append(current)
    return "\n".join(lines)


def _is_problem_status(line: str) -> bool:
    """True when a status / ready line should be emphasized in red."""
    s = (line or "").strip()
    if not s:
        return False
    if s in _PROBLEM_PHASES:
        return True
    # "0/2 ready" / "1/3 ready" when ready < desired
    if s.lower().endswith("ready") and "/" in s:
        head = s.split()[0]
        if "/" in head:
            left, _, right = head.partition("/")
            try:
                return int(left) < int(right)
            except ValueError:
                pass
    low = s.lower()
    return any(
        token in low
        for token in (
            "crashloop",
            "imagepull",
            "errimage",
            "oomkilled",
            "unhealthy",
            "notready",
            "unavailable",
            "failed",
            "error",
            "evicted",
        )
    )


def _node_label(name: str, *extra_lines: str) -> str:
    """Name (wrapped) plus optional status / type lines (plain text, below icon)."""
    chunks: List[str] = [_wrap_name(name)]
    for line in extra_lines:
        line = (line or "").strip()
        if line:
            chunks.append(_wrap_name(line) if len(line) > LABEL_WIDTH else line)
    return "\n".join(chunks)


def _problem_fontcolor(*status_lines: str) -> Dict[str, str]:
    """Node attrs: red font when any status line is problematic (keeps labelloc=b)."""
    if any(_is_problem_status(s) for s in status_lines):
        return {"fontcolor": _STATUS_RED}
    return {}


def _make_pod(p: dict):
    phase = p.get("phase") or "?"
    return Pod(
        _node_label(p.get("name") or "pod", phase),
        **_problem_fontcolor(phase),
    )


def _workload_status_line(ready: int, desired: int) -> str:
    return f"{ready}/{desired} ready"

def _svc_for_workload(topo: dict, workload_name: str) -> List[dict]:
    return [s for s in topo.get("services") or [] if workload_name in (s.get("targets") or [])]


def _routes_for_service(topo: dict, svc_name: str) -> List[dict]:
    return [r for r in topo.get("routes") or [] if r.get("service") == svc_name]


def _ingresses_for_service(topo: dict, svc_name: str) -> List[dict]:
    return [i for i in topo.get("ingresses") or [] if svc_name in (i.get("services") or [])]


def _template_hash(
    obj_name: str,
    labels: Optional[dict] = None,
    workload_name: str = "",
) -> str:
    """Resolve pod-template-hash from labels or from a Deployment-prefixed name."""
    labels = labels or {}
    h = labels.get("pod-template-hash")
    if h:
        return str(h)
    name = obj_name or ""
    if workload_name and name.startswith(workload_name + "-"):
        rest = name[len(workload_name) + 1 :]
        # ReplicaSet: <deploy>-<hash> ; Pod: <deploy>-<hash>-<id>
        return rest.split("-")[0] if rest else ""
    return ""


def _draw_exposed_workload(topo: dict, wl: dict) -> None:
    """
    Traffic / ownership chain with >> :

        Route/Ingress >> Service >> Deployment >> ReplicaSet >> Pod
    """
    name = wl["name"]
    kind = wl["kind"]
    pods = wl.get("pods") or []
    ready, desired = wl.get("ready", 0), wl.get("desired", 0)

    services = _svc_for_workload(topo, name)
    expose_nodes = []
    for svc in services:
        for route in _routes_for_service(topo, svc["name"]):
            host = route.get("host") or route["name"]
            expose_nodes.append(("route", host))
        for ing in _ingresses_for_service(topo, svc["name"]):
            host = (ing.get("hosts") or [ing["name"]])[0]
            expose_nodes.append(("ingress", host))

    expose_icons = []
    for kind_x, host in expose_nodes:
        kind_lbl = "Route" if kind_x == "route" else "Ingress"
        expose_icons.append(Ingress(_node_label(kind_lbl, host)))

    svc_nodes = [
        Service(_node_label(svc["name"], svc.get("type") or "")) for svc in services
    ]

    status = _workload_status_line(ready, desired)
    bad = _problem_fontcolor(status)
    if kind == "DaemonSet":
        ctrl = DaemonSet(_node_label(name, status), **bad)
    elif kind == "DeploymentConfig":
        ctrl = Deployment(_node_label(f"{name} (DC)", status), **bad)
    else:
        ctrl = Deployment(_node_label(name, status), **bad)

    hpa_nodes = [HPA(_node_label(h["name"])) for h in (wl.get("hpas") or [])[:1]]

    rs_meta = (wl.get("replicasets") or [])[:2]
    rs_hashes = {
        _template_hash(rs["name"], rs.get("labels"), name) for rs in rs_meta
    }
    rs_hashes.discard("")

    if rs_hashes:
        relevant_pods = [
            p
            for p in pods
            if _template_hash(p.get("name") or "", p.get("labels"), name) in rs_hashes
        ]
    else:
        relevant_pods = list(pods)

    drawn_pods = relevant_pods[:MAX_PODS_DRAWN]
    overflow = len(relevant_pods) - len(drawn_pods)

    rs_to_pods: List[tuple] = []
    claimed = set()
    for rs in rs_meta:
        rs_hash = _template_hash(rs["name"], rs.get("labels"), name)
        matched = []
        for p in drawn_pods:
            ph = _template_hash(p.get("name") or "", p.get("labels"), name)
            if rs_hash and ph and rs_hash == ph:
                matched.append(p)
                claimed.add(p.get("name"))
        rs_to_pods.append((rs, matched))

    orphan_pods = [p for p in drawn_pods if p.get("name") not in claimed]

    # Route >> Service >> Deployment
    for expose in expose_icons:
        if svc_nodes:
            expose >> svc_nodes[0]
    for s in svc_nodes:
        s >> ctrl

    if hpa_nodes:
        hpa_nodes[0] >> ctrl

    # Deployment >> ReplicaSet >> Pod
    if rs_to_pods:
        for rs, matched in rs_to_pods:
            rs_node = ReplicaSet(_node_label(rs["name"]))
            ctrl >> rs_node
            for p in matched:
                rs_node >> _make_pod(p)
        for p in orphan_pods:
            ctrl >> _make_pod(p)
    else:
        for p in drawn_pods:
            ctrl >> _make_pod(p)

    if overflow > 0:
        ctrl >> Pod(_node_label(f"+{overflow} more"))


def _draw_stateful_workload(topo: dict, wl: dict) -> None:
    """
    Stateful chain with >> :

        Route >> Service >> StatefulSet >> Pod >> PVC >> PV >> StorageClass
    """
    name = wl["name"]
    ready, desired = wl.get("ready", 0), wl.get("desired", 0)
    pods = wl.get("pods") or []
    pvcs = wl.get("pvcs") or []
    services = _svc_for_workload(topo, name)

    svc_node = (
        Service(_node_label(services[0]["name"], services[0].get("type") or ""))
        if services
        else None
    )
    status = _workload_status_line(ready, desired)
    sts = StatefulSet(_node_label(name, status), **_problem_fontcolor(status))

    if svc_node:
        for svc in services:
            for route in _routes_for_service(topo, svc["name"]):
                host = route.get("host") or route["name"]
                Ingress(_node_label("Route", host)) >> svc_node
        svc_node >> sts

    drawn = pods[:MAX_PODS_DRAWN] or [{"name": f"{name}-0", "phase": "?"}]
    for i, p in enumerate(drawn):
        pod = _make_pod(p)
        sts >> pod
        pvc_meta = pvcs[i] if i < len(pvcs) else (pvcs[0] if pvcs else None)
        if not pvc_meta:
            continue
        pvc = PVC(_node_label(pvc_meta["name"]))
        pod >> pvc
        vol = pvc_meta.get("volume_name")
        sc_name = pvc_meta.get("storage_class")
        if vol:
            pv_node = PV(_node_label(vol))
            pvc >> pv_node
            if sc_name:
                pv_node >> StorageClass(_node_label(sc_name))


def generate_namespace_diagram(
    topo: dict,
    output_filename: str = "openshift_namespace_topology",
) -> None:
    ns = topo.get("namespace") or "namespace"
    stats = topo.get("stats") or {}
    title = (
        f"Namespace topology: {ns}\n"
        f"Workloads {stats.get('workloads', 0)} · "
        f"Pods {stats.get('pods', 0)} · "
        f"Services {stats.get('services', 0)} · "
        f"Routes {stats.get('routes', 0)}"
    )

    graph_attr = {
        "bgcolor": rh.WHITE,
        "pad": "0.6",
        "nodesep": "0.85",
        "ranksep": "1.0",
        "fontname": rh.FONT_TEXT,
        "fontsize": "11",
        "fontcolor": rh.TEXT,
        "overlap": "false",
        "sep": "+14",
    }
    node_attr = {
        "fontname": rh.FONT_TEXT,
        "fontsize": "10",
        "fontcolor": rh.TEXT,
        "labelloc": "b",
        "margin": "0.12,0.08",
    }
    edge_attr = {
        "fontname": rh.FONT_TEXT,
        "fontsize": "9",
        "color": rh.GRAY_40,
        "arrowsize": "0.7",
    }
    cluster_attr = {
        "fontsize": "11",
        "fontname": rh.FONT_TEXT,
        "fontcolor": rh.TEXT,
        "style": "rounded",
        "bgcolor": rh.SURFACE,
        "pencolor": rh.SURFACE_BORDER,
        "margin": "18",
    }

    groups: Dict[str, List[dict]] = topo.get("groups") or {}
    if not groups:
        print(f"No workloads found in namespace '{ns}'. Nothing to draw.")
        return

    with Diagram(
        title,
        filename=output_filename,
        show=False,
        direction="LR",
        curvestyle="ortho",
        graph_attr=graph_attr,
        node_attr=node_attr,
        edge_attr=edge_attr,
    ):
        # Console: each app.kubernetes.io/part-of becomes a grouping ring
        for group_name, workloads in sorted(groups.items(), key=lambda x: x[0]):
            with Cluster(f"Application · {group_name}", graph_attr=cluster_attr):
                for wl in sorted(workloads, key=lambda w: w["name"]):
                    kind = wl["kind"]
                    with Cluster(
                        f"{kind} · {wl['name']}",
                        graph_attr={
                            **cluster_attr,
                            "bgcolor": rh.TEAL_10 if kind == "StatefulSet" else rh.INTERACTION_BLUE_10,
                            "pencolor": rh.TEAL_40 if kind == "StatefulSet" else rh.INTERACTION_BLUE_40,
                        },
                    ):
                        if kind == "StatefulSet":
                            _draw_stateful_workload(topo, wl)
                        else:
                            _draw_exposed_workload(topo, wl)

    out = os.path.join(os.getcwd(), f"{output_filename}.png")
    print(f"Diagram generated: {out}")
    print(
        "DRAFT note: layout follows mingrammer Exposed-Pod / Stateful patterns; "
        "grouping mirrors Console Developer Topology (app.kubernetes.io/part-of)."
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "DRAFT: Generate a Console-like namespace topology diagram from "
            "must-gather or oc adm inspect data."
        )
    )
    parser.add_argument(
        "source",
        nargs="?",
        help="Path to must-gather / inspect archive (.zip/.tar.gz) or extracted directory",
    )
    parser.add_argument(
        "-n",
        "--namespace",
        required=True,
        help="Namespace / project to render (required)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output filename prefix (default: openshift_ns_<namespace>)",
    )
    args = parser.parse_args(argv if argv is not None else None)

    source = args.source
    if not source:
        source = input("Path to must-gather or oc adm inspect output: ").strip()
    if not source:
        print("No source path provided.")
        return 1

    topo = analyze_namespace(source, args.namespace)
    if not topo:
        return 1

    out = args.output or f"openshift_ns_{args.namespace.replace('/', '_')}"
    generate_namespace_diagram(topo, output_filename=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
