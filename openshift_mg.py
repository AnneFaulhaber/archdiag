"""
Shared OpenShift must-gather parsing for architecture diagrams.

Extracts cluster metadata, exclusive node role buckets, network, storage,
and ingress details from ZIP / TAR / TAR.GZ archives.
"""

from __future__ import annotations

import re
import tarfile
import zipfile
from typing import Any, Dict, List, Optional, Tuple, Union

import yaml

ArchiveHandle = Union[zipfile.ZipFile, tarfile.TarFile]

CONFIG_PATHS = {
    "version": "cluster-scoped-resources/config.openshift.io/clusterversions/version.yaml",
    "network": "cluster-scoped-resources/config.openshift.io/networks.yaml",
    "ingress": "cluster-scoped-resources/config.openshift.io/ingresses.yaml",
    "nodes_prefix": "cluster-scoped-resources/core/nodes/",
    "infrastructure": "cluster-scoped-resources/config.openshift.io/infrastructures/cluster.yaml",
    "console": "cluster-scoped-resources/config.openshift.io/consoles/cluster.yaml",
    "storage_prefix": "cluster-scoped-resources/storage.k8s.io/storageclasses",
}

# Exclusive classification priority (first match wins).
ROLE_BUCKETS = (
    ("control-plane", ("master", "control-plane")),
    ("infra", ("infra",)),
    ("worker", ()),  # any role equal to / starting with "worker"
    ("other", ()),
)


def open_archive(path: str) -> Tuple[ArchiveHandle, str]:
    """Open a must-gather archive. Returns (handle, archive_type)."""
    lower = path.lower()
    if lower.endswith(".zip"):
        return zipfile.ZipFile(path, "r"), "ZIP"
    if lower.endswith(".tar.gz") or lower.endswith(".tgz"):
        return tarfile.open(path, "r:gz"), "TAR.GZ"
    if lower.endswith(".tar"):
        return tarfile.open(path, "r:"), "TAR"
    raise ValueError("Input file must be a .zip, .tar.gz, .tgz, or .tar file.")


def get_member_list(file_handle: ArchiveHandle) -> List[str]:
    if isinstance(file_handle, zipfile.ZipFile):
        return file_handle.namelist()
    if isinstance(file_handle, tarfile.TarFile):
        return file_handle.getnames()
    return []


def find_and_parse_yaml(file_handle: ArchiveHandle, path: str) -> Optional[dict]:
    try:
        if isinstance(file_handle, zipfile.ZipFile):
            try:
                with file_handle.open(path) as f:
                    content = f.read().decode("utf-8")
            except KeyError:
                return None
        elif isinstance(file_handle, tarfile.TarFile):
            try:
                member = file_handle.getmember(path)
            except KeyError:
                return None
            f = file_handle.extractfile(member)
            if f is None:
                return None
            with f:
                content = f.read().decode("utf-8")
        else:
            raise TypeError("Unsupported archive file handle type.")
        return yaml.safe_load(content)
    except Exception as e:
        print(f"Error parsing {path}: {e}")
        return None


def get_dynamic_prefix(file_handle: ArchiveHandle, expected_suffix: str) -> str:
    """Find the must-gather path prefix; prefer openshift-release-dev."""
    candidates: List[str] = []
    for member in get_member_list(file_handle):
        if member.endswith(expected_suffix):
            prefix = member[: -len(expected_suffix)]
            if "openshift-release-dev" in prefix:
                return prefix
            candidates.append(prefix)
    return candidates[0] if candidates else ""


def parse_memory_to_gib(mem_str: Any) -> str:
    """Convert kube memory quantities to a short GiB/TiB label."""
    if not mem_str or not isinstance(mem_str, str):
        return str(mem_str) if mem_str is not None else "N/A"
    try:
        m = re.match(r"^\s*([0-9.]+)\s*([KkMmGgTt]i?)?\s*$", mem_str)
        if not m:
            return mem_str
        val = float(m.group(1))
        unit = (m.group(2) or "").lower()

        if unit in ("k", "ki"):
            gib = val / (1024.0 * 1024.0)
        elif unit in ("m", "mi"):
            gib = val / 1024.0
        elif unit in ("g", "gi"):
            gib = val
        elif unit in ("t", "ti"):
            gib = val * 1024.0
        elif unit == "":
            # Bare number from capacity is usually already Gi-ish; keep as-is.
            gib = val
        else:
            return mem_str

        if gib >= 512:
            return f"{gib / 1024.0:.1f} Ti"
        if gib >= 10:
            return f"{gib:.0f} Gi"
        return f"{gib:.1f} Gi"
    except Exception:
        return mem_str


def short_hostname(name: str, cluster_name: Optional[str] = None) -> str:
    """
    Prefer a short host label for diagram cells.

    Strips DNS domain and optional infrastructureName / cluster prefix
    (e.g. 6dccbca39d-qmxqn-worker-1-0-2jxpw → worker-1-0-2jxpw).
    """
    if not name:
        return "N/A"
    host = name.split(".")[0]
    if cluster_name and cluster_name not in ("N/A", "Unknown Cluster", "version"):
        prefix = f"{cluster_name}-"
        if host.startswith(prefix) and len(host) > len(prefix):
            host = host[len(prefix) :]
    return host


def health_sort_key(node: dict) -> Tuple[int, str]:
    """NotReady / Unknown first, then name."""
    ready = node.get("ready", "Unknown")
    priority = 0 if ready != "Ready" else 1
    return (priority, node.get("short_name") or node.get("name") or "")


def apply_display_names(nodes: List[dict], cluster_name: Optional[str] = None) -> None:
    """Mutate nodes in place with shorter display names."""
    for node in nodes:
        raw = node.get("name") or ""
        node["short_name"] = short_hostname(raw, cluster_name)


def sort_buckets_health_first(buckets: Dict[str, List[dict]]) -> Dict[str, List[dict]]:
    return {
        key: sorted(nodes, key=health_sort_key)
        for key, nodes in buckets.items()
    }


def fleet_stats(nodes: List[dict]) -> dict:
    ready = sum(1 for n in nodes if n.get("ready") == "Ready")
    not_ready = sum(1 for n in nodes if n.get("ready") == "NotReady")
    unknown = len(nodes) - ready - not_ready
    return {
        "total": len(nodes),
        "ready": ready,
        "not_ready": not_ready,
        "unknown": unknown,
    }


def bucket_nodes(nodes: List[dict]) -> Dict[str, List[dict]]:
    """Partition nodes into exclusive role buckets."""
    buckets = {
        "control-plane": [],
        "infra": [],
        "worker": [],
        "other": [],
    }
    for node in nodes:
        buckets.setdefault(node.get("bucket", "other"), []).append(node)
    return buckets


def _spec_from_resource(doc: Optional[dict]) -> dict:
    if not doc:
        return {}
    kind = doc.get("kind", "")
    if kind.endswith("List") and doc.get("items"):
        return doc["items"][0].get("spec") or {}
    return doc.get("spec") or {}


def _status_from_resource(doc: Optional[dict]) -> dict:
    if not doc:
        return {}
    kind = doc.get("kind", "")
    if kind.endswith("List") and doc.get("items"):
        return doc["items"][0].get("status") or {}
    return doc.get("status") or {}


def classify_roles(roles: List[str]) -> Tuple[str, str, List[str]]:
    """
    Exclusive role bucketing.

    Returns (bucket, primary_label, secondary_labels).
    bucket: control-plane | infra | worker | other
    """
    normalized = [r.lower() for r in roles if r]
    if not normalized:
        return "other", "node", []

    if "master" in normalized or "control-plane" in normalized:
        bucket = "control-plane"
        primary = "control-plane" if "control-plane" in normalized else "master"
    elif "infra" in normalized:
        bucket = "infra"
        primary = "infra"
    elif any(r == "worker" or r.startswith("worker") for r in normalized):
        bucket = "worker"
        # Prefer plain "worker" as primary when present.
        primary = "worker" if "worker" in normalized else next(
            r for r in normalized if r == "worker" or r.startswith("worker")
        )
    else:
        bucket = "other"
        primary = normalized[0]

    secondary = [r for r in normalized if r != primary]
    # Drop aliases of the primary bucket (master ↔ control-plane).
    if bucket == "control-plane":
        secondary = [r for r in secondary if r not in ("master", "control-plane")]
    elif bucket == "worker":
        secondary = [r for r in secondary if r != "worker"]
    elif bucket == "infra":
        secondary = [r for r in secondary if r != "infra"]

    # Keep stable, readable order: known specialty roles first.
    specialty_order = ("haproxy", "ingress", "storage", "infra")
    secondary_sorted = sorted(
        secondary,
        key=lambda r: (
            0 if r in specialty_order else 1,
            0 if r.startswith("worker") else 1,
            r,
        ),
    )
    return bucket, primary, secondary_sorted


def _node_ready(status: dict) -> str:
    for cond in status.get("conditions") or []:
        if cond.get("type") == "Ready":
            return "Ready" if cond.get("status") == "True" else "NotReady"
    return "Unknown"


def _internal_ip(status: dict) -> str:
    for addr in status.get("addresses") or []:
        if addr.get("type") == "InternalIP":
            return addr.get("address") or "N/A"
    return "N/A"


def extract_node_data(file_handle: ArchiveHandle, nodes_prefix: str) -> List[dict]:
    nodes: List[dict] = []
    for member in get_member_list(file_handle):
        if not (member.startswith(nodes_prefix) and member.endswith(".yaml")):
            continue
        node_yaml = find_and_parse_yaml(file_handle, member)
        if not node_yaml:
            continue
        try:
            meta = node_yaml.get("metadata") or {}
            status = node_yaml.get("status") or {}
            name = meta.get("name")
            if not name:
                continue

            roles = [
                key.replace("node-role.kubernetes.io/", "")
                for key in (meta.get("labels") or {})
                if key.startswith("node-role.kubernetes.io/")
            ]
            if not roles:
                roles = ["node"]

            bucket, primary, secondary = classify_roles(roles)
            capacity = status.get("capacity") or {}
            allocatable = status.get("allocatable") or {}
            node_info = status.get("nodeInfo") or {}

            nodes.append(
                {
                    "name": name,
                    "short_name": short_hostname(name),
                    "roles": roles,
                    "bucket": bucket,
                    "primary_role": primary,
                    "secondary_roles": secondary,
                    "cpu": capacity.get("cpu", "N/A"),
                    "memory": parse_memory_to_gib(capacity.get("memory", "N/A")),
                    "cpu_allocatable": allocatable.get("cpu", "N/A"),
                    "memory_allocatable": parse_memory_to_gib(
                        allocatable.get("memory", "N/A")
                    ),
                    "ready": _node_ready(status),
                    "internal_ip": _internal_ip(status),
                    "os_image": node_info.get("osImage", "N/A"),
                    "architecture": node_info.get("architecture", "N/A"),
                    "kubelet": node_info.get("kubeletVersion", "N/A"),
                }
            )
        except KeyError as e:
            print(f"Skipping node {member} due to missing key: {e}")

    nodes.sort(key=lambda n: (n["bucket"], n["short_name"]))
    return nodes


def extract_storage_class_data(
    file_handle: ArchiveHandle, storage_prefix: str
) -> List[dict]:
    storage_classes: List[dict] = []
    for member in get_member_list(file_handle):
        if not (member.startswith(storage_prefix) and member.endswith(".yaml")):
            continue
        sc_yaml = find_and_parse_yaml(file_handle, member)
        if not sc_yaml:
            continue
        try:
            meta = sc_yaml.get("metadata") or {}
            annotations = meta.get("annotations") or {}
            is_default = (
                annotations.get("storageclass.kubernetes.io/is-default-class")
                == "true"
            )
            storage_classes.append(
                {
                    "name": meta.get("name", "N/A"),
                    "provisioner": sc_yaml.get("provisioner", "N/A"),
                    "default": is_default,
                    "reclaim_policy": sc_yaml.get("reclaimPolicy", "N/A"),
                }
            )
        except KeyError as e:
            print(f"Skipping storage class {member} due to missing key: {e}")

    storage_classes.sort(key=lambda s: (not s["default"], s["name"]))
    return storage_classes


def extract_network_info(network_doc: Optional[dict]) -> dict:
    spec = _spec_from_resource(network_doc)
    status = _status_from_resource(network_doc)

    cluster_cidrs = []
    for entry in spec.get("clusterNetwork") or []:
        if isinstance(entry, dict) and entry.get("cidr"):
            cluster_cidrs.append(entry["cidr"])

    machine_cidrs = []
    for entry in spec.get("machineNetwork") or []:
        if isinstance(entry, dict) and entry.get("cidr"):
            machine_cidrs.append(entry["cidr"])

    return {
        "networkType": spec.get("networkType")
        or status.get("networkType")
        or "N/A",
        "clusterNetwork": cluster_cidrs,
        "serviceNetwork": list(spec.get("serviceNetwork") or []),
        "machineNetwork": machine_cidrs,
        "mtu": (status.get("clusterNetworkMTU") or spec.get("mtu") or "N/A"),
    }


def extract_ingress_info(ingress_doc: Optional[dict]) -> dict:
    if not ingress_doc:
        return {"domain": "N/A", "appsDomain": "N/A", "componentRoutes": []}

    # Prefer cluster Ingress config (config.openshift.io), not controllers list.
    if ingress_doc.get("kind") == "IngressList" and ingress_doc.get("items"):
        item = ingress_doc["items"][0]
    else:
        item = ingress_doc

    spec = item.get("spec") or {}
    status = item.get("status") or {}
    domain = (
        spec.get("domain")
        or status.get("domain")
        or item.get("domain")
        or "N/A"
    )
    return {
        "domain": domain,
        "appsDomain": spec.get("appsDomain") or domain,
        "componentRoutes": [
            r.get("name")
            for r in (spec.get("componentRoutes") or [])
            if isinstance(r, dict) and r.get("name")
        ],
    }


def analyze_must_gather(must_gather_path: str) -> Optional[dict]:
    """
    Parse a must-gather archive into a diagram-ready data dict.
    Returns None on fatal errors (messages printed to stdout).
    """
    data: Dict[str, Any] = {
        "cluster_name": "Unknown Cluster",
        "cluster_id": "N/A",
        "version": "N/A",
        "channel": "N/A",
        "version_state": "N/A",
        "platform": "N/A",
        "api_url": "N/A",
        "api_internal_url": "N/A",
        "console_url": "N/A",
        "control_plane_topology": "N/A",
        "infrastructure_topology": "N/A",
        "network": {},
        "ingress": {},
        "nodes": [],
        "buckets": {
            "control-plane": [],
            "infra": [],
            "worker": [],
            "other": [],
        },
        "storage_classes": [],
    }

    print(f"Analyzing Must Gather: {must_gather_path}")
    file_handle: Optional[ArchiveHandle] = None
    archive_type = None

    try:
        file_handle, archive_type = open_archive(must_gather_path)
        print(f"Detected archive type: {archive_type}")

        prefix = get_dynamic_prefix(file_handle, CONFIG_PATHS["version"])
        if not prefix:
            print("Error: Could not find OpenShift configuration files within the archive.")
            print(f"Expected file ending in: {CONFIG_PATHS['version']}")
            return None
        print(f"Detected Must Gather internal path prefix: {prefix}")

        # ClusterVersion
        version_data = find_and_parse_yaml(
            file_handle, prefix + CONFIG_PATHS["version"]
        )
        if version_data:
            status = version_data.get("status") or {}
            desired = status.get("desired") or {}
            data["version"] = desired.get("version") or status.get("version") or "N/A"
            data["cluster_id"] = (version_data.get("spec") or {}).get(
                "clusterID", "N/A"
            )
            data["channel"] = (version_data.get("spec") or {}).get("channel", "N/A")
            history = status.get("history") or []
            if history:
                data["version_state"] = history[0].get("state", "N/A")
            # Progressing / Available conditions
            for cond in status.get("conditions") or []:
                if cond.get("type") == "Available" and cond.get("status") == "True":
                    data["version_state"] = data["version_state"] or "Completed"
                    break

        # Infrastructure
        infra_data = find_and_parse_yaml(
            file_handle, prefix + CONFIG_PATHS["infrastructure"]
        )
        if infra_data:
            status = infra_data.get("status") or {}
            platform = status.get("platform") or (infra_data.get("spec") or {}).get(
                "platform", "N/A"
            )
            data["platform"] = platform or "N/A"
            data["cluster_name"] = (
                status.get("infrastructureName")
                or data["cluster_name"]
            )
            data["api_url"] = status.get("apiServerURL") or "N/A"
            data["api_internal_url"] = status.get("apiServerInternalURI") or "N/A"
            data["control_plane_topology"] = (
                status.get("controlPlaneTopology") or "N/A"
            )
            data["infrastructure_topology"] = (
                status.get("infrastructureTopology") or "N/A"
            )

        # Console URL (optional path)
        console_data = find_and_parse_yaml(
            file_handle, prefix + CONFIG_PATHS["console"]
        )
        if console_data:
            console_status = console_data.get("status") or {}
            data["console_url"] = console_status.get("consoleURL") or "N/A"

        # Network
        network_doc = find_and_parse_yaml(
            file_handle, prefix + CONFIG_PATHS["network"]
        )
        data["network"] = extract_network_info(network_doc)

        # Storage
        print("Extracting storage class information...")
        data["storage_classes"] = extract_storage_class_data(
            file_handle, prefix + CONFIG_PATHS["storage_prefix"]
        )
        print(f"Found {len(data['storage_classes'])} storage classes.")

        # Ingress
        ingress_doc = find_and_parse_yaml(
            file_handle, prefix + CONFIG_PATHS["ingress"]
        )
        data["ingress"] = extract_ingress_info(ingress_doc)

        # Nodes
        print("Extracting node information...")
        data["nodes"] = extract_node_data(
            file_handle, prefix + CONFIG_PATHS["nodes_prefix"]
        )
        apply_display_names(data["nodes"], data.get("cluster_name"))
        data["buckets"] = sort_buckets_health_first(bucket_nodes(data["nodes"]))
        print(
            f"Found {len(data['nodes'])} nodes "
            f"(control-plane={len(data['buckets']['control-plane'])}, "
            f"infra={len(data['buckets']['infra'])}, "
            f"worker={len(data['buckets']['worker'])}, "
            f"other={len(data['buckets']['other'])})."
        )

        return data

    except FileNotFoundError:
        print(f"Error: Must Gather archive file not found at {must_gather_path}")
        return None
    except ValueError as e:
        print(f"Error: {e}")
        return None
    except (zipfile.BadZipFile, tarfile.ReadError) as e:
        print(
            f"Error: The file {must_gather_path} is not a valid "
            f"{archive_type or 'archive'} file. Details: {e}"
        )
        return None
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return None
    finally:
        if file_handle is not None:
            try:
                file_handle.close()
            except Exception:
                pass
