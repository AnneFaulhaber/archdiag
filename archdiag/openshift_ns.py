"""
DRAFT: Namespace-scoped OpenShift resource parsing.

Reads a must-gather archive or an `oc adm inspect <namespace>` tree/archive
and builds a Console-like topology model for one namespace.

Expected layout (either source):
  .../namespaces/<ns>/core/pods/
  .../namespaces/<ns>/core/services/
  .../namespaces/<ns>/apps/deployments/
  .../namespaces/<ns>/apps/statefulsets/
  .../namespaces/<ns>/apps/replicasets/
  .../namespaces/<ns>/apps/daemonsets/
  .../namespaces/<ns>/route.openshift.io/routes/
  .../namespaces/<ns>/networking.k8s.io/ingresses/
  .../namespaces/<ns>/core/persistentvolumeclaims/
  .../namespaces/<ns>/autoscaling/horizontalpodautoscalers/
  .../cluster-scoped-resources/core/persistentvolumes/   (optional)
  .../cluster-scoped-resources/storage.k8s.io/storageclasses/

This module is intentionally draft-quality: path discovery is forgiving,
selectors are best-effort, and Knative/BuildConfig edges are stubs for v2.
"""

from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .openshift_mg import (
    ArchiveHandle,
    find_and_parse_yaml,
    get_member_list,
    open_archive,
)

# Console / Kubernetes recommended labels used by Developer Topology.
LABEL_PART_OF = "app.kubernetes.io/part-of"
LABEL_NAME = "app.kubernetes.io/name"
LABEL_INSTANCE = "app.kubernetes.io/instance"
LABEL_COMPONENT = "app.kubernetes.io/component"
LABEL_RUNTIME = "app.openshift.io/runtime"
LABEL_APP = "app"


def _labels(obj: dict) -> Dict[str, str]:
    meta = obj.get("metadata") or {}
    labels = meta.get("labels") or {}
    return {str(k): str(v) for k, v in labels.items()}


def _name(obj: dict) -> str:
    return ((obj.get("metadata") or {}).get("name")) or "unnamed"


def _ns(obj: dict) -> Optional[str]:
    return (obj.get("metadata") or {}).get("namespace")


def _kind(obj: dict) -> str:
    return (obj.get("kind") or "").strip()


def _selector_labels(obj: dict) -> Dict[str, str]:
    """Best-effort match labels from Service / ReplicaSet / Deployment selectors."""
    spec = obj.get("spec") or {}
    sel = spec.get("selector")
    if isinstance(sel, dict):
        # Service: spec.selector is a map
        if "matchLabels" not in sel and all(isinstance(v, str) for v in sel.values()):
            return {str(k): str(v) for k, v in sel.items()}
        # Deployment/RS: spec.selector.matchLabels
        ml = sel.get("matchLabels") or {}
        return {str(k): str(v) for k, v in ml.items()}
    return {}


def _matches_labels(pod_labels: Dict[str, str], selector: Dict[str, str]) -> bool:
    if not selector:
        return False
    return all(pod_labels.get(k) == v for k, v in selector.items())


def application_group(obj: dict) -> str:
    """
    Console Topology groups components by app.kubernetes.io/part-of when set,
    otherwise falls back to app.kubernetes.io/name, app, or the resource name.
    """
    labels = _labels(obj)
    for key in (LABEL_PART_OF, LABEL_INSTANCE, LABEL_NAME, LABEL_APP):
        if labels.get(key):
            return labels[key]
    return _name(obj)


def _unwrap_resource_docs(doc: Optional[dict]) -> List[dict]:
    """
    Expand Kubernetes List wrappers from oc adm inspect / must-gather.

    Inspect emits DeploymentList / PodList / RouteList (not bare "List"),
    and empty lists often use items: null.
    """
    if not doc or not isinstance(doc, dict):
        return []
    kind = str(doc.get("kind") or "")
    if "items" in doc and (kind.endswith("List") or kind == "List"):
        items = doc.get("items")
        if not items:
            return []
        return [i for i in items if isinstance(i, dict)]
    # Skip non-resource documents
    if not kind:
        return []
    return [doc]


def _list_yaml_under_prefix(handle: ArchiveHandle, prefix: str) -> List[dict]:
    """Load every .yaml/.yml under prefix (files or List wrappers)."""
    out: List[dict] = []
    for member in get_member_list(handle):
        if not member.startswith(prefix):
            continue
        if not (member.endswith(".yaml") or member.endswith(".yml")):
            continue
        if member.endswith("/"):
            continue
        out.extend(_unwrap_resource_docs(find_and_parse_yaml(handle, member)))
    return out


def _load_resource_candidates(
    handle: ArchiveHandle, ns_root: str, candidates: Tuple[str, ...]
) -> List[dict]:
    """
    Load resources for one kind.

    Prefer oc-adm-inspect consolidated files (e.g. apps/deployments.yaml).
    Fall back to must-gather per-object directories (apps/deployments/*.yaml).
    Avoid the inspect pods/<name>/ log tree when core/pods.yaml exists.
    """
    members = set(get_member_list(handle))
    collected: List[dict] = []

    # 1) Exact List YAML files
    for frag in candidates:
        for ext in (".yaml", ".yml"):
            path = ns_root + frag + ext
            if path in members:
                collected.extend(
                    _unwrap_resource_docs(find_and_parse_yaml(handle, path))
                )
    if collected:
        return _dedupe_objects(collected)

    # 2) Directory of individual objects (must-gather style)
    for frag in candidates:
        # Skip bare "pods" — inspect keeps logs under namespaces/<ns>/pods/
        if frag in ("pods", "services", "deployments", "statefulsets", "replicasets", "daemonsets"):
            continue
        prefix = ns_root + frag
        if any(m.startswith(prefix + "/") or m.startswith(prefix) for m in members):
            collected.extend(_list_yaml_under_prefix(handle, prefix))
    # must-gather dirs use plural folder names like core/pods/<name>.yaml
    if not collected:
        for frag in candidates:
            prefix = ns_root + frag
            # Only treat as directory if members exist under prefix/
            dir_hits = [
                m
                for m in members
                if m.startswith(prefix + "/")
                and (m.endswith(".yaml") or m.endswith(".yml"))
            ]
            if dir_hits:
                collected.extend(_list_yaml_under_prefix(handle, prefix + "/"))
    return _dedupe_objects(collected)


def _dedupe_objects(objs: List[dict]) -> List[dict]:
    seen = set()
    out: List[dict] = []
    for obj in objs:
        meta = obj.get("metadata") or {}
        key = (
            obj.get("kind"),
            meta.get("uid") or meta.get("namespace"),
            meta.get("name"),
        )
        if key in seen or not meta.get("name"):
            continue
        seen.add(key)
        out.append(obj)
    return out


def _find_namespace_roots(
    members: Iterable[str], namespace: str
) -> List[str]:
    """
    Return archive path prefixes that end with namespaces/<ns>/.
    Works for must-gather and oc adm inspect trees packed as archives.
    """
    needle = f"namespaces/{namespace}/"
    roots = set()
    for m in members:
        idx = m.find(needle)
        if idx >= 0:
            roots.add(m[: idx + len(needle)])
    return sorted(roots)


def _load_ns_resources(handle: ArchiveHandle, ns_root: str) -> Dict[str, List[dict]]:
    """Map logical type → list of objects under one namespaces/<ns>/ root."""
    buckets = {
        "pods": ("core/pods",),
        "services": ("core/services",),
        "deployments": ("apps/deployments",),
        "statefulsets": ("apps/statefulsets",),
        "replicasets": ("apps/replicasets",),
        "daemonsets": ("apps/daemonsets",),
        "routes": ("route.openshift.io/routes",),
        "ingresses": ("networking.k8s.io/ingresses",),
        "pvcs": ("core/persistentvolumeclaims",),
        "hpas": ("autoscaling/horizontalpodautoscalers",),
        "deploymentconfigs": ("apps.openshift.io/deploymentconfigs",),
    }
    return {
        key: _load_resource_candidates(handle, ns_root, frags)
        for key, frags in buckets.items()
    }

def _load_cluster_storage(handle: ArchiveHandle) -> Tuple[List[dict], List[dict]]:
    """Optional cluster-scoped PV + StorageClass for stateful diagrams."""
    members = get_member_list(handle)
    # Discover a cluster-scoped root from any known path.
    sc_root = ""
    pv_root = ""
    for m in members:
        if "cluster-scoped-resources/storage.k8s.io/storageclasses" in m:
            sc_root = m.split("cluster-scoped-resources/")[0] + (
                "cluster-scoped-resources/storage.k8s.io/storageclasses"
            )
            break
    for m in members:
        if "cluster-scoped-resources/core/persistentvolumes" in m:
            pv_root = m.split("cluster-scoped-resources/")[0] + (
                "cluster-scoped-resources/core/persistentvolumes"
            )
            break
    scs = _list_yaml_under_prefix(handle, sc_root) if sc_root else []
    pvs = _list_yaml_under_prefix(handle, pv_root) if pv_root else []
    return pvs, scs


def _pod_phase(pod: dict) -> str:
    return ((pod.get("status") or {}).get("phase")) or "?"


def _ready_replicas(workload: dict) -> Tuple[int, int]:
    status = workload.get("status") or {}
    spec = workload.get("spec") or {}
    desired = status.get("replicas")
    if desired is None:
        desired = spec.get("replicas", 0)
    ready = status.get("readyReplicas") or status.get("availableReplicas") or 0
    try:
        return int(ready), int(desired or 0)
    except (TypeError, ValueError):
        return 0, 0


def build_topology(namespace: str, resources: Dict[str, List[dict]], pvs: List[dict], scs: List[dict]) -> dict:
    """
    Build a Console-inspired topology graph for one namespace.

    Nodes are workloads; edges connect Route/Ingress → Service → Workload → Pods,
    plus PVC/PV/SC for StatefulSets (mingrammer stateful pattern).
    """
    pods = resources.get("pods") or []
    services = resources.get("services") or []
    deployments = resources.get("deployments") or []
    statefulsets = resources.get("statefulsets") or []
    replicasets = resources.get("replicasets") or []
    daemonsets = resources.get("daemonsets") or []
    routes = resources.get("routes") or []
    ingresses = resources.get("ingresses") or []
    pvcs = resources.get("pvcs") or []
    hpas = resources.get("hpas") or []
    dcs = resources.get("deploymentconfigs") or []

    workloads: List[dict] = []

    def add_workload(obj: dict, kind: str) -> None:
        ready, desired = _ready_replicas(obj)
        workloads.append(
            {
                "kind": kind,
                "name": _name(obj),
                "namespace": _ns(obj) or namespace,
                "labels": _labels(obj),
                "group": application_group(obj),
                "selector": _selector_labels(obj),
                "ready": ready,
                "desired": desired,
                "runtime": _labels(obj).get(LABEL_RUNTIME),
                "raw": obj,
            }
        )

    for d in deployments:
        add_workload(d, "Deployment")
    for s in statefulsets:
        add_workload(s, "StatefulSet")
    for d in daemonsets:
        add_workload(d, "DaemonSet")
    for d in dcs:
        add_workload(d, "DeploymentConfig")

    # Attach pods by label selector
    for wl in workloads:
        matched = [
            {
                "name": _name(p),
                "phase": _pod_phase(p),
                "labels": _labels(p),
            }
            for p in pods
            if _matches_labels(_labels(p), wl["selector"])
        ]
        # Prefer Running first for diagram order
        matched.sort(key=lambda x: (0 if x["phase"] == "Running" else 1, x["name"]))
        wl["pods"] = matched

        # Active ReplicaSets owned by this Deployment (skip scaled-to-zero history)
        wl["replicasets"] = []
        for rs in replicasets:
            owners = ((rs.get("metadata") or {}).get("ownerReferences") or [])
            owned = any(
                o.get("name") == wl["name"]
                and o.get("kind") in ("Deployment", "DeploymentConfig")
                for o in owners
            )
            if not owned and not _matches_labels(_labels(rs), wl["selector"]):
                continue
            status = rs.get("status") or {}
            spec = rs.get("spec") or {}
            replicas = status.get("replicas")
            if replicas is None:
                replicas = spec.get("replicas") or 0
            try:
                replicas = int(replicas)
            except (TypeError, ValueError):
                replicas = 0
            if replicas <= 0:
                continue
            wl["replicasets"].append({"name": _name(rs), "labels": _labels(rs)})

        # HPAs targeting this workload
        wl["hpas"] = []
        for h in hpas:
            ref = ((h.get("spec") or {}).get("scaleTargetRef") or {})
            if ref.get("name") == wl["name"]:
                wl["hpas"].append({"name": _name(h), "raw": h})

        # PVCs for StatefulSets: by volumeClaimTemplates name prefix or pod volumes
        wl["pvcs"] = []
        if wl["kind"] == "StatefulSet":
            vcts = ((wl["raw"].get("spec") or {}).get("volumeClaimTemplates") or [])
            prefixes = [_name(v) if "metadata" in v else (v.get("metadata") or {}).get("name") for v in vcts]
            # volumeClaimTemplates items are like PVC specs embedded — name from metadata
            prefixes = []
            for v in vcts:
                n = ((v.get("metadata") or {}).get("name")) or ""
                if n:
                    prefixes.append(n)
            for pvc in pvcs:
                pvc_name = _name(pvc)
                if any(pvc_name.startswith(f"{pref}-{wl['name']}-") or pvc_name.startswith(f"{pref}-") for pref in prefixes):
                    wl["pvcs"].append(
                        {
                            "name": pvc_name,
                            "volume_name": ((pvc.get("spec") or {}).get("volumeName")),
                            "storage_class": ((pvc.get("spec") or {}).get("storageClassName")),
                            "raw": pvc,
                        }
                    )

    # Services → workloads: Service.spec.selector vs workload pods / matchLabels
    svc_models = []
    for svc in services:
        sel = _selector_labels(svc)
        targets = []
        if sel:
            for wl in workloads:
                if (
                    _matches_labels(wl["labels"], sel)
                    or all(wl["selector"].get(k) == v for k, v in sel.items())
                    or any(_matches_labels(p["labels"], sel) for p in wl.get("pods") or [])
                ):
                    targets.append(wl["name"])
        svc_models.append(
            {
                "name": _name(svc),
                "selector": sel,
                "targets": sorted(set(targets)),
                "type": ((svc.get("spec") or {}).get("type")) or "ClusterIP",
                "raw": svc,
            }
        )

    # Routes → services by spec.to.name
    route_models = []
    for r in routes:
        to = ((r.get("spec") or {}).get("to") or {})
        host = ((r.get("spec") or {}).get("host")) or ""
        route_models.append(
            {
                "name": _name(r),
                "host": host,
                "service": to.get("name"),
                "raw": r,
            }
        )

    ingress_models = []
    for ing in ingresses:
        backends = []
        for rule in ((ing.get("spec") or {}).get("rules") or []):
            http = rule.get("http") or {}
            for path in http.get("paths") or []:
                svc = ((path.get("backend") or {}).get("service") or {})
                if svc.get("name"):
                    backends.append(svc["name"])
        default = ((ing.get("spec") or {}).get("defaultBackend") or {}).get("service") or {}
        if default.get("name"):
            backends.append(default["name"])
        ingress_models.append(
            {
                "name": _name(ing),
                "hosts": [rule.get("host") for rule in ((ing.get("spec") or {}).get("rules") or []) if rule.get("host")],
                "services": sorted(set(backends)),
                "raw": ing,
            }
        )

    # Group workloads like Console application rings
    groups: Dict[str, List[dict]] = defaultdict(list)
    for wl in workloads:
        groups[wl["group"]].append(wl)

    pv_by_name = {_name(pv): pv for pv in pvs}
    sc_by_name = {_name(sc): sc for sc in scs}

    return {
        "namespace": namespace,
        "groups": dict(groups),
        "workloads": workloads,
        "services": svc_models,
        "routes": route_models,
        "ingresses": ingress_models,
        "persistent_volumes": pv_by_name,
        "storage_classes": sc_by_name,
        "stats": {
            "workloads": len(workloads),
            "pods": len(pods),
            "services": len(services),
            "routes": len(routes),
            "statefulsets": len(statefulsets),
            "deployments": len(deployments),
        },
    }


def analyze_namespace_archive(path: str, namespace: str) -> Optional[dict]:
    """Parse must-gather or inspect archive for one namespace."""
    try:
        handle, _kind = open_archive(path)
    except ValueError as e:
        print(e)
        return None

    with handle:
        members = get_member_list(handle)
        roots = _find_namespace_roots(members, namespace)
        if not roots:
            print(
                f"Namespace '{namespace}' not found under namespaces/ in archive. "
                f"Tip: oc adm inspect ns/{namespace}  or include it in must-gather."
            )
            return None
        # Prefer the longest/most specific root; merge if multiple copies exist
        merged: Dict[str, List[dict]] = defaultdict(list)
        for root in roots:
            chunk = _load_ns_resources(handle, root)
            for k, items in chunk.items():
                merged[k].extend(items)
        pvs, scs = _load_cluster_storage(handle)
        return build_topology(namespace, dict(merged), pvs, scs)


def analyze_namespace_dir(path: str, namespace: str) -> Optional[dict]:
    """Parse an extracted inspect/must-gather directory."""
    root = Path(path)
    if not root.is_dir():
        print(f"Not a directory: {path}")
        return None

    candidates = []
    for dirpath, _dirnames, _filenames in os.walk(root):
        if dirpath.endswith(os.path.join("namespaces", namespace)):
            candidates.append(Path(dirpath))

    if not candidates:
        print(f"Namespace '{namespace}' not found under {path}")
        return None

    import yaml

    def load_list_or_dir(*rel_parts: str) -> List[dict]:
        """Prefer inspect consolidated YAML; else must-gather directory."""
        base = ns_root.joinpath(*rel_parts)
        out: List[dict] = []
        for ext in (".yaml", ".yml"):
            f = Path(str(base) + ext)
            if f.is_file():
                try:
                    doc = yaml.safe_load(f.read_text(encoding="utf-8"))
                except Exception:
                    continue
                out.extend(_unwrap_resource_docs(doc))
        if out:
            return _dedupe_objects(out)
        if base.is_dir():
            for f in base.rglob("*"):
                if f.suffix not in (".yaml", ".yml"):
                    continue
                # Skip inspect pod log trees accidentally
                if "logs" in f.parts:
                    continue
                try:
                    doc = yaml.safe_load(f.read_text(encoding="utf-8"))
                except Exception:
                    continue
                out.extend(_unwrap_resource_docs(doc))
        return _dedupe_objects(out)

    ns_root = candidates[0]
    resources = {
        "pods": load_list_or_dir("core", "pods"),
        "services": load_list_or_dir("core", "services"),
        "deployments": load_list_or_dir("apps", "deployments"),
        "statefulsets": load_list_or_dir("apps", "statefulsets"),
        "replicasets": load_list_or_dir("apps", "replicasets"),
        "daemonsets": load_list_or_dir("apps", "daemonsets"),
        "routes": load_list_or_dir("route.openshift.io", "routes"),
        "ingresses": load_list_or_dir("networking.k8s.io", "ingresses"),
        "pvcs": load_list_or_dir("core", "persistentvolumeclaims"),
        "hpas": load_list_or_dir("autoscaling", "horizontalpodautoscalers"),
        "deploymentconfigs": load_list_or_dir("apps.openshift.io", "deploymentconfigs"),
    }

    pvs: List[dict] = []
    scs: List[dict] = []
    for dirpath, _dn, _fn in os.walk(root):
        p = Path(dirpath)
        if p.name == "persistentvolumes" and "core" in p.parts:
            pvs = load_list_or_dir(*p.relative_to(ns_root).parts) if False else []
            # load from absolute path helper
            tmp = []
            for f in p.rglob("*.yaml"):
                try:
                    tmp.extend(_unwrap_resource_docs(yaml.safe_load(f.read_text(encoding="utf-8"))))
                except Exception:
                    pass
            pvs = _dedupe_objects(tmp)
        if p.name == "storageclasses" and "storage.k8s.io" in p.parts:
            tmp = []
            for f in list(p.rglob("*.yaml")) + list(p.rglob("*.yml")):
                try:
                    tmp.extend(_unwrap_resource_docs(yaml.safe_load(f.read_text(encoding="utf-8"))))
                except Exception:
                    pass
            # also consolidated file next to dir
            for ext in (".yaml", ".yml"):
                f = Path(str(p) + ext)
                if f.is_file():
                    try:
                        tmp.extend(_unwrap_resource_docs(yaml.safe_load(f.read_text(encoding="utf-8"))))
                    except Exception:
                        pass
            scs = _dedupe_objects(tmp)

    return build_topology(namespace, resources, pvs, scs)


def analyze_namespace(path: str, namespace: str) -> Optional[dict]:
    """Auto-detect archive vs directory."""
    if os.path.isdir(path):
        return analyze_namespace_dir(path, namespace)
    return analyze_namespace_archive(path, namespace)
