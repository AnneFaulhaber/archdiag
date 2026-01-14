#!/usr/bin/env python3
import zipfile
import io
import yaml
import glob
import os
import tarfile
from graphviz import Digraph
from diagrams import Diagram, Cluster
from diagrams.k8s.infra import Master, Node
from diagrams.k8s.network import Ingress, Service
from diagrams.k8s.storage import StorageClass, PV  # PV used as a storage symbol
import math

import re

# --- FILE PATH DEFINITIONS (Suffixes within the Must Gather Structure) ---
CONFIG_PATHS = {
    'version': 'cluster-scoped-resources/config.openshift.io/clusterversions/version.yaml',
    'network': 'cluster-scoped-resources/config.openshift.io/networks.yaml',
    'ingress': 'cluster-scoped-resources/config.openshift.io/ingresses.yaml',
    'nodes_prefix': 'cluster-scoped-resources/core/nodes/',
    'infrastructure': 'cluster-scoped-resources/config.openshift.io/infrastructures/cluster.yaml',
    'storage_prefix': 'cluster-scoped-resources/storage.k8s.io/storageclasses'
}

def find_and_parse_yaml(file_handle, path: str):
    """
    Finds a specific YAML file inside the archive (ZIP or TAR) and parses it.
    file_handle: zipfile.ZipFile or tarfile.TarFile
    path: internal path string inside the archive
    """
    try:
        if isinstance(file_handle, zipfile.ZipFile):
            try:
                with file_handle.open(path) as f:
                    content = f.read().decode('utf-8')
            except KeyError:
                print(f"Warning: Configuration file not found at {path}")
                return None
        elif isinstance(file_handle, tarfile.TarFile):
            # tarfile.extractfile returns None if not found
            member = None
            try:
                member = file_handle.getmember(path)
            except KeyError:
                # getmember raises KeyError if not exists, so we will try to find by name fallback
                member = None

            if member is not None:
                f = file_handle.extractfile(member)
            else:
                # fallback: try to find a member whose name matches exactly
                try:
                    f = file_handle.extractfile(path)
                except Exception:
                    f = None

            if f is None:
                print(f"Warning: Configuration file not found at {path}")
                return None

            with f:
                content = f.read().decode('utf-8')
        else:
            raise TypeError("Unsupported archive file handle type.")

        return yaml.safe_load(content)

    except Exception as e:
        print(f"Error parsing {path}: {e}")
        return None

def get_member_list(file_handle):
    """Returns the list of members (file paths) from the archive handle."""
    if isinstance(file_handle, zipfile.ZipFile):
        return file_handle.namelist()
    elif isinstance(file_handle, tarfile.TarFile):
        return file_handle.getnames()
    return []

def extract_pod_distribution(file_handle, nodes_prefix="cluster-scoped-resources/core/namespaces/"):
    """Return a dict mapping node name -> list of pod names."""
    pod_distribution = {}
    members = get_member_list(file_handle)
    for member in members:
        if "/pods/" in member and member.endswith(".yaml"):
            pod_yaml = find_and_parse_yaml(file_handle, member)
            if pod_yaml:
                try:
                    pod_name = pod_yaml['metadata']['name']
                    node_name = pod_yaml['spec'].get('nodeName', 'N/A')
                    if node_name not in pod_distribution:
                        pod_distribution[node_name] = []
                    pod_distribution[node_name].append(pod_name)
                except KeyError:
                    continue
    return pod_distribution

def extract_cluster_version_history(file_handle, version_path):
    """Return a list of version strings from the ClusterVersion history."""
    version_yaml = find_and_parse_yaml(file_handle, version_path)
    history_list = []
    if version_yaml:
        history = version_yaml.get('status', {}).get('history', [])
        for entry in history:
            version = entry.get('version')
            state = entry.get('state')
            if version:
                history_list.append(f"{version} ({state})")
    return history_list

def extract_network_components(file_handle, network_path, ingress_path):
    """Return dict with cluster network info and ingress controllers."""
    network_info = {}
    net_yaml = find_and_parse_yaml(file_handle, network_path)
    ingress_yaml = find_and_parse_yaml(file_handle, ingress_path)
    
    if net_yaml:
        cluster_cidrs_list = net_yaml.get('spec', {}).get('clusterNetwork', [])
        service_cidrs = net_yaml.get('spec', {}).get('serviceNetwork', [])
        network_info['cluster_cidrs'] = [c.get('cidr') for c in cluster_cidrs_list if isinstance(c, dict)]
        network_info['service_cidrs'] = service_cidrs
        network_info['network_type'] = net_yaml.get('spec', {}).get('networkType', 'N/A')
    
    ingress_controllers = []
    if ingress_yaml and ingress_yaml.get('items'):
        for ic in ingress_yaml.get('items', []):
            name = ic.get('metadata', {}).get('name', 'N/A')
            namespace = ic.get('metadata', {}).get('namespace', 'N/A')
            ingress_controllers.append(f"{name} ({namespace})")
    
    network_info['ingress_controllers'] = ingress_controllers
    return network_info



def extract_storage_class_data(file_handle, storage_prefix: str) -> list:
    """Extracts all storage class definitions from the dedicated folder."""
    storage_classes = []
    member_list = get_member_list(file_handle)

    for member in member_list:
        if member.startswith(storage_prefix) and member.endswith('.yaml'):
            sc_yaml = find_and_parse_yaml(file_handle, member)
            if sc_yaml:
                try:
                    name = sc_yaml['metadata']['name']
                    is_default = sc_yaml['metadata'].get('annotations', {}).get('storageclass.kubernetes.io/is-default-class') == 'true'
                    provisioner = sc_yaml.get('provisioner', 'N/A')
                    storage_classes.append({
                        'name': name,
                        'provisioner': provisioner,
                        'default': is_default
                    })
                except KeyError as e:
                    print(f"Skipping storage class {member} due to missing key: {e}")

    return storage_classes

def parse_memory_to_gib(mem_str: str):
    """
    Convert memory strings like '16310732Ki', '16384Mi', '16Gi' to Gi (float string with 2 decimals).
    If parsing fails, return the original string.
    """
    if not mem_str or not isinstance(mem_str, str):
        return mem_str
    try:
        m = re.match(r'^\s*([0-9.]+)\s*([KkMmGgTt]i?)?$', mem_str)
        if not m:
            return mem_str
        val = float(m.group(1))
        unit = (m.group(2) or '').lower()

        # normalize units to kibibyte-based
        if unit in ('k', 'ki'):
            gib = val / (1024.0 * 1024.0)
        elif unit in ('m', 'mi'):
            gib = val / 1024.0
        elif unit in ('g', 'gi') or unit == '':
            # If unit omitted assume Gi or value already Gi
            if unit == '':
                # ambiguous; assume number is in bytes? more likely it's in Ki/Mi/Gi as kube reports.
                gib = val
            else:
                gib = val
        elif unit in ('t', 'ti'):
            gib = val * 1024.0
        else:
            return mem_str

        return f"{gib:.2f} Gi"
    except Exception:
        return mem_str

def extract_node_data(file_handle, nodes_prefix: str):
    """Extracts node information, including roles, CPU, and memory configuration."""
    node_data = []
    member_list = get_member_list(file_handle)

    for member in member_list:
        if member.startswith(nodes_prefix) and member.endswith('.yaml'):
            node_yaml = find_and_parse_yaml(file_handle, member)
            if node_yaml:
                try:
                    name = node_yaml['metadata']['name']
                    roles = [
                        role.replace('node-role.kubernetes.io/', '')
                        for role, value in node_yaml['metadata'].get('labels', {}).items()
                        if role.startswith('node-role.kubernetes.io/')
                    ]
                    if not roles:
                        roles = ['node']

                    capacity = node_yaml.get('status', {}).get('capacity', {}) or {}
                    cpu = capacity.get('cpu', 'N/A')
                    memory_raw = capacity.get('memory', 'N/A')
                    memory = parse_memory_to_gib(memory_raw)

                    node_data.append({
                        'name': name,
                        'roles': roles,
                        'cpu': cpu,
                        'memory': memory
                    })
                except KeyError as e:
                    print(f"Skipping node {member} due to missing key: {e}")
    return node_data

def get_dynamic_prefix(file_handle, expected_suffix: str) -> str:
    """
    Dynamically determines the variable path prefix in the must-gather archive
    by searching for a known file suffix, prioritizing the path containing
    'openshift-release-dev' if multiple candidates exist.
    """
    member_list = get_member_list(file_handle)
    candidate_prefixes = []

    for member in member_list:
        if member.endswith(expected_suffix):
            prefix = member[:-len(expected_suffix)]
            candidate_prefixes.append(prefix)
            if 'openshift-release-dev' in prefix:
                return prefix

    if candidate_prefixes:
        return candidate_prefixes[0]
    return ""



def generate_diagrams_k8s_icons_multirow_workers(data: dict, output_filename="openshift_k8s_icons"):
    """
    Generates an OpenShift cluster diagram using k8s icons.
    - Masters and hybrid master+worker nodes are differentiated with color.
    - Worker nodes are split into multiple horizontal rows if there are many.
    - Nodes connect to storage classes instead of ingress.
    """
    outdir = os.getcwd()

    # Cluster metadata label
    cluster_name = data.get("cluster_name", "Unknown Cluster")
    version = data.get("version", "N/A")
    cluster_id = data.get("cluster_id", "N/A")
    platform = data.get("platform", "N/A")
    cluster_label = (
        f"{cluster_name}\n"
        f"Version: {version}\n"
        f"ID: {cluster_id}\n"
        f"Platform: {platform}"
    )

    ingress_domain = "N/A"
    ingress_data = data.get("ingress") or {}
    if isinstance(ingress_data, dict):
        ingress_domain = (
            ingress_data.get("domain")
            or ingress_data.get("spec", {}).get("domain")
            or ingress_data.get("status", {}).get("domain")
            or ingress_domain
        )

    nodes = data.get("nodes", [])
    master_nodes = [n for n in nodes if "master" in n["roles"] and "worker" not in n["roles"]]
    infra_nodes = [n for n in nodes if "infra" in n["roles"]]
    worker_nodes = [n for n in nodes if "worker" in n["roles"] and "master" not in n["roles"]]
    hybrid_master_worker = [n for n in nodes if "master" in n["roles"] and "worker" in n["roles"]]

    with Diagram(
        "OpenShift Cluster Diagram",
        filename=output_filename,
        show=False,
        direction="TB"
    ):
        with Cluster(cluster_label):

            # Networking
            ingress = Ingress(ingress_domain)
            svc = Service("Cluster Service")

            # Storage
            scs = data.get("storage_classes", [])
            if scs:
                storage_icons = []
                for sc in scs:
                    label = sc.get("name", "sc")
                    if sc.get("default"):
                        label += " (default)"
                    storage_icons.append(StorageClass(label))
            else:
                storage_icons = [PV("Persistent Storage")]

            # Control Plane (Masters)
            with Cluster("Control Plane"):
                master_icons = []
                for m in master_nodes:
                    master_icons.append(Master(m["name"]))
                for m in hybrid_master_worker:
                    # Use custom color for hybrid master+worker
                    master_icons.append(Master(f"{m['name']} (W)"))

            # Infra Nodes
            if infra_nodes:
                with Cluster("Infra Nodes"):
                    infra_icons = [Node(n["name"]) for n in infra_nodes]
            else:
                infra_icons = []

            # Worker Nodes (split into rows)
            MAX_NODES_PER_ROW = 5
            worker_icon_rows = []
            if worker_nodes:
                with Cluster(f"Workers Nodes"):
                    num_rows = math.ceil(len(worker_nodes) / MAX_NODES_PER_ROW)
                    for i in range(num_rows):
                        row_nodes = worker_nodes[i * MAX_NODES_PER_ROW: (i + 1) * MAX_NODES_PER_ROW]
                        row_icons = [Node(n["name"]) for n in row_nodes]
                        worker_icon_rows.append(row_icons)

            # --- Layout wiring ---
            ingress >> svc  # optional connection

            all_nodes = master_icons + infra_icons
            for row in worker_icon_rows:
                all_nodes += row

            # Connect all nodes to storage
            for node_icon in all_nodes:
                for s in storage_icons:
                    node_icon >> s

            # Connect masters to infra nodes
            #if master_icons and infra_icons:
            #    master_icons[0] >> infra_icons[0]

            # Connect masters to first row of workers
            #if master_icons and worker_icon_rows:
            #    master_icons[0] >> worker_icon_rows[0][0]

            # Connect worker rows vertically
            #for i in range(len(worker_icon_rows) - 1):
            #    worker_icon_rows[i][0] >> worker_icon_rows[i + 1][0]

            

    print(f"Diagram generated: {os.path.join(outdir, output_filename)}.png")



### Generates a diagram based on graphiz, alignment is off and difficult to fix
def generate_diagram(data: dict, output_filename="openshift_architecture.gv"):
    """
    Generates a Graphviz diagram based on the parsed OpenShift data.
    """
    dot = Digraph(comment='OpenShift Cluster Architecture', graph_attr={
        'rankdir': 'TB',
        'bgcolor': '#f0f4f8',
        'fontname': 'Inter',
        'splines': 'ortho',
        'nodesep': '0.7',
        'ranksep': '1.0'
    })
    dot.attr('node', shape='box', style='filled', fontname='Inter')

    # CLUSTER METADATA
    cluster_name = data.get('cluster_name') or 'Unknown Cluster'
    cluster_id = data.get('cluster_id') or 'N/A'
    version = data.get('version') or 'N/A'
    platform = data.get('platform', 'N/A')

    metadata_label = f"""<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="4" CELLPADDING="4">
        <TR><TD COLSPAN="2" ALIGN="LEFT"><FONT POINT-SIZE="16" FACE="Inter Bold">OpenShift Cluster: {cluster_name}</FONT></TD></TR>
        <TR><TD ALIGN="LEFT">Version:</TD><TD ALIGN="LEFT">{version}</TD></TR>
        <TR><TD ALIGN="LEFT">ID:</TD><TD ALIGN="LEFT">{cluster_id}</TD></TR>
        <TR><TD ALIGN="LEFT">Platform:</TD><TD ALIGN="LEFT">{platform}</TD></TR>
    </TABLE>>"""

    dot.node('metadata', metadata_label, shape='box', style='filled', fillcolor='#e3f2fd', color='#90caf9', fontsize='12')

    # NODE GROUPS
    nodes = data.get('nodes', [])
    master_nodes = [n for n in nodes if 'master' in n['roles']]
    non_master_nodes = [n for n in nodes if 'master' not in n['roles']]
    infra_nodes = [n for n in non_master_nodes if 'infra' in n['roles']]
    worker_nodes = [n for n in non_master_nodes if 'infra' not in n['roles'] and 'worker' in n['roles']]

    MAX_NODES_PER_ROW = 4
    PRIMARY_MASTER_ROW_SIZE = 4  # number of masters to show in the top master row (remaining go to following rows)

    def draw_nodes_with_row_limit(dot_graph: Digraph, subgraph_id: str, node_list: list, fill_color: str, color: str, first_row_limit: int = None, row_limit: int = 4):
        """
        Draw nodes splitting into rows. If first_row_limit provided, the first row will contain up to that many nodes;
        remaining nodes will be chunked by row_limit.
        Returns (first_node_name, last_node_name) or (None, None) if empty.
        """
        if not node_list:
            return None, None

        # Build rows list depending on first_row_limit
        rows = []
        idx = 0
        total = len(node_list)
        if first_row_limit and total > 0:
            first_count = min(first_row_limit, total)
            rows.append(node_list[0:first_count])
            idx = first_count

        # remaining rows by row_limit
        while idx < total:
            rows.append(node_list[idx: idx + row_limit])
            idx += row_limit

        previous_row_first_node_id = None
        first_node_of_first_row = rows[0][0]['name']
        last_node_of_last_row = rows[-1][0]['name']

        for i, row in enumerate(rows):
            current_row_rank_id = f"{subgraph_id}_rank_{i}"
            with dot_graph.subgraph(name=current_row_rank_id) as row_sub:
                row_sub.attr(rank='same')
                for j, node in enumerate(row):
                    node_id = node['name']
                    role_display_string = ", ".join([r.capitalize() for r in node['roles']])
                    label = f"Roles: {role_display_string}\n{node['name']}\nCPU: {node['cpu']}, Mem: {node['memory']}"
                    row_sub.node(node_id, label, fillcolor=fill_color, color=color)
                    # invisibly connect horizontally to set ordering
                    if j > 0:
                        row_sub.edge(row[j-1]['name'], node_id, style='invis')

            # connect first nodes of rows vertically to encourage stacking order
            current_row_first_node_id = row[0]['name']
            if previous_row_first_node_id:
                dot_graph.edge(previous_row_first_node_id, current_row_first_node_id, style='invis')
            previous_row_first_node_id = current_row_first_node_id

        return first_node_of_first_row, last_node_of_last_row

    master_first, master_last = (None, None)
    if master_nodes:
        with dot.subgraph(name='cluster_masters') as sub:
            sub.attr(label=f'Control Plane (Masters) ({len(master_nodes)} Total)', style='filled', color='#ffcc80', fillcolor='#fff3e0', fontname='Inter Bold')
            master_first, master_last = draw_nodes_with_row_limit(dot, 'master', master_nodes, '#ffb74d', '#e65100', first_row_limit=PRIMARY_MASTER_ROW_SIZE, row_limit=MAX_NODES_PER_ROW)

    infra_first, infra_last = (None, None)
    if infra_nodes:
        with dot.subgraph(name='cluster_infras') as sub:
            sub.attr(label=f'Infra Nodes ({len(infra_nodes)} Total)', style='filled', color='#c5cae9', fillcolor='#ede7f6', fontname='Inter Bold')
            infra_first, infra_last = draw_nodes_with_row_limit(dot, 'infra', infra_nodes, '#9fa8da', '#303f9f', row_limit=MAX_NODES_PER_ROW)

    worker_first, worker_last = (None, None)
    if worker_nodes:
        with dot.subgraph(name='cluster_workers') as sub:
            sub.attr(label=f'Worker Nodes ({len(worker_nodes)} Total)', style='filled', color='#80cbc4', fillcolor='#e0f2f1', fontname='Inter Bold')
            worker_first, worker_last = draw_nodes_with_row_limit(dot, 'worker', worker_nodes, '#4db6ac', '#004d40', row_limit=MAX_NODES_PER_ROW)

    # NETWORK / STORAGE / INGRESS blocks placed beneath metadata visually, but NO connectors to node groups
    net_data = data.get('network', {})
    cluster_cidrs_list = net_data.get('clusterNetwork', [])
    all_cidrs = []
    if cluster_cidrs_list and isinstance(cluster_cidrs_list, list):
        for entry in cluster_cidrs_list:
            if isinstance(entry, dict) and entry.get('cidr'):
                all_cidrs.append(entry['cidr'])
    cluster_cidr_str = ', '.join(all_cidrs) if all_cidrs else 'N/A'
    service_cidr_str = ', '.join(net_data.get('serviceNetwork', ['N/A']))

    net_label = f"""<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="4">
        <TR><TD COLSPAN="2" BGCOLOR="#e0e0e0"><FONT POINT-SIZE="14" FACE="Inter Bold">Network Configuration</FONT></TD></TR>
        <TR><TD ALIGN="LEFT">Type:</TD><TD ALIGN="LEFT">{net_data.get('networkType', 'N/A')}</TD></TR>
        <TR><TD ALIGN="LEFT">Cluster CIDR:</TD><TD ALIGN="LEFT">{cluster_cidr_str}</TD></TR>
        <TR><TD ALIGN="LEFT">Service CIDR:</TD><TD ALIGN="LEFT">{service_cidr_str}</TD></TR>
    </TABLE>>"""
    dot.node('network', net_label, shape='plain', fontsize='12')

    storage_classes = data.get('storage_classes', [])
    if storage_classes:
        sc_rows = ""
        for sc in storage_classes:
            default_marker = " (Default)" if sc.get('default') else ""
            sc_rows += f'<TR><TD ALIGN="LEFT">{sc["name"]}{default_marker}</TD></TR>'

        storage_label = f"""<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="4">
            <TR><TD COLSPAN="1" BGCOLOR="#ffe0b2"><FONT POINT-SIZE="14" FACE="Inter Bold">Configured Storage Classes</FONT></TD></TR>
            {sc_rows}
        </TABLE>>"""
        dot.node('storage', storage_label, shape='plain', fontsize='12')
    else:
        storage_label = "Storage:\nNot Configured"
        dot.node('storage', storage_label, shape='cylinder', style='filled', fillcolor='#ffe0b2', color='#ff9800')

    ingress_data = data.get('ingress', {})
    ingress_domain = 'N/A'
    if ingress_data:
        # ingress spec may have domain under 'domain' or 'spec.domain'
        ingress_domain = ingress_data.get('domain') or ingress_data.get('spec', {}).get('domain') or ingress_data.get('status', {}).get('domain') or 'N/A'
    ingress_label = f"Ingress Domain:\n{ingress_domain}"
    dot.node('ingress', ingress_label, shape='house', style='filled', fillcolor='#c8e6c9', color='#4caf50')

    # RANKING: metadata on top, network/storage/ingress below it (same rank),
    # then node groups below (we use invisible edges for relative vertical ordering
    # but we DO NOT create semantic edges between infra/network/storage/ingress and node groups).
    # --- RANKING FIX ---
    # Metadata at the top
    with dot.subgraph(name='rank_metadata') as r_meta:
        r_meta.attr(rank='same')
        r_meta.node('metadata')

    # Network / storage / ingress directly below metadata
    with dot.subgraph(name='rank_middle') as r_mid:
        r_mid.attr(rank='same')
        r_mid.node('network')
        r_mid.node('storage')
        r_mid.node('ingress')

    # Use invisible edge to force vertical order
    dot.edge('metadata', 'network', style='invis')

    # Node groups BELOW the middle tier
    with dot.subgraph(name='rank_nodes') as r_nodes:
        r_nodes.attr(rank='same')
        for n in [master_first, infra_first, worker_first]:
            if n:
                r_nodes.node(n)
                break  # Only need one anchor

    # Force vertical order: network → node groups
    if master_first or infra_first or worker_first:
        dot.edge('network', master_first or infra_first or worker_first, style='invis')

    # Optional logical edges between clusters (kept minimal): show workers -> storage, but user asked no connectors for storage/network/ingress; comment out or remove.
    # If you want to show a minimal logical connection you can uncomment these:
    # if worker_first:
    #     dot.edge('cluster_workers', 'storage', label='Accesses persistent storage')

    # Render the graph
    print(f"\nRendering diagram to {output_filename}.png and {output_filename}.pdf...")
    dot.render(output_filename, format='png', cleanup=True)
    dot.render(output_filename, format='pdf', cleanup=True)
    print("Done. Check your current directory for the generated files.")

def analyze_must_gather(must_gather_path: str):
    """
    Main function to analyze the Must Gather archive (ZIP, TAR.GZ, TGZ, or TAR) and generate data.
    """
    data = {
        'cluster_name': None,
        'cluster_id': None,
        'version': None,
        'platform': 'N/A',
        'network': {},
        'ingress': {},
        'nodes': [],
        'storage_classes': []
    }

    print(f"Analyzing Must Gather: {must_gather_path}")

    archive_type = None
    file_handle = None

    try:
        if must_gather_path.endswith('.zip'):
            file_handle = zipfile.ZipFile(must_gather_path, 'r')
            archive_type = 'ZIP'
        elif must_gather_path.endswith('.tar.gz') or must_gather_path.endswith('.tgz'):
            file_handle = tarfile.open(must_gather_path, 'r:gz')
            archive_type = 'TAR.GZ'
        elif must_gather_path.endswith('.tar'):
            file_handle = tarfile.open(must_gather_path, 'r:')
            archive_type = 'TAR'
        else:
            print("Error: Input file must be a .zip, .tar.gz, .tgz, or .tar file.")
            return

        print(f"Detected archive type: {archive_type}")

        version_suffix = CONFIG_PATHS['version']
        prefix = get_dynamic_prefix(file_handle, version_suffix)

        if not prefix:
            print(f"Error: Could not find OpenShift configuration files within the archive.")
            print(f"Expected file ending in: {version_suffix}")
            return

        print(f"Detected Must Gather internal path prefix: {prefix}")

        # Cluster Version and ID
        full_version_path = prefix + CONFIG_PATHS['version']
        version_data = find_and_parse_yaml(file_handle, full_version_path)
        if version_data:
            data['version'] = version_data.get('status', {}).get('desired', {}).get('version', 'N/A')
            data['cluster_id'] = version_data.get('spec', {}).get('clusterID', 'N/A')
            data['cluster_name'] = version_data.get('metadata', {}).get('name', 'N/A')
            if data['cluster_name'] == 'version':
                data['cluster_name'] = 'OpenShift Cluster'

        # Infrastructure / platform
        full_infra_path = prefix + CONFIG_PATHS['infrastructure']
        infra_data = find_and_parse_yaml(file_handle, full_infra_path)
        if infra_data:
            platform = infra_data.get('status', {}).get('platform', 'N/A')
            if platform == 'N/A':
                platform = infra_data.get('spec', {}).get('platform', 'N/A')
            data['platform'] = platform

        if data['cluster_name'] == 'OpenShift Cluster' and infra_data:
            data['cluster_name'] = infra_data.get('status', {}).get('infrastructureName', data['cluster_name'])

        # Network configuration
        full_network_path = prefix + CONFIG_PATHS['network']
        network_data = find_and_parse_yaml(file_handle, full_network_path)
        network_spec = None
        if network_data:
            if network_data.get('kind') == 'NetworkList' and network_data.get('items'):
                network_spec = network_data['items'][0].get('spec')
            elif network_data.get('spec'):
                network_spec = network_data.get('spec')
            else:
                # fallback to data itself if structure is different
                network_spec = network_data
        if network_spec:
            data['network'] = network_spec

        # Storage classes
        storage_base_path = prefix + CONFIG_PATHS['storage_prefix']
        print("Extracting storage class information...")
        data['storage_classes'] = extract_storage_class_data(file_handle, storage_base_path)
        print(f"Found {len(data['storage_classes'])} storage classes.")

        # Ingress
        full_ingress_path = prefix + CONFIG_PATHS['ingress']
        ingress_data = find_and_parse_yaml(file_handle, full_ingress_path)
        ingress_spec = None
        if ingress_data:
            if ingress_data.get('kind') == 'IngressList' and ingress_data.get('items'):
                ingress_spec = ingress_data['items'][0].get('spec')
            elif ingress_data.get('spec'):
                ingress_spec = ingress_data.get('spec')
            else:
                ingress_spec = ingress_data
        if ingress_spec:
            data['ingress'] = ingress_spec

        # Nodes
        nodes_base_path = prefix + CONFIG_PATHS['nodes_prefix']
        print("Extracting node information...")
        data['nodes'] = extract_node_data(file_handle, nodes_base_path)
        print(f"Found {len(data['nodes'])} nodes.")

        # Generate diagram
        generate_diagrams_k8s_icons_multirow_workers(data)

    except FileNotFoundError:
        print(f"Error: Must Gather archive file not found at {must_gather_path}")
    except (zipfile.BadZipFile, tarfile.ReadError) as e:
        print(f"Error: The file {must_gather_path} is not a valid {archive_type} file. Details: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    finally:
        if file_handle:
            try:
                file_handle.close()
            except Exception:
                pass


if __name__ == "__main__":
    must_gather_file = input("Please enter the path to the OpenShift Must Gather ZIP or TAR.GZ/TAR file: ")
    if not must_gather_file:
        print("No file path provided. Exiting.")
    else:
        analyze_must_gather(must_gather_file.strip())
