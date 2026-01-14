import zipfile
import io
import yaml
import glob
import os
import tarfile # New import for tar.gz support
from graphviz import Digraph

# --- FILE PATH DEFINITIONS (Suffixes within the Must Gather Structure) ---
# These are the *fixed suffixes* of the configuration YAMLs relative to the dynamic root.
CONFIG_PATHS = {
    'version': 'cluster-scoped-resources/config.openshift.io/clusterversions/version.yaml',
    'network': 'cluster-scoped-resources/config.openshift.io/networks.yaml',
    'ingress': 'cluster-scoped-resources/config.openshift.io/ingresses.yaml',
    'nodes_prefix': 'cluster-scoped-resources/core/nodes/',
    'infrastructure': 'cluster-scoped-resources/config.openshift.io/infrastructures/cluster.yaml', # Added infrastructure path
    'storage_prefix': 'cluster-scoped-resources/storage.k8s.io/storageclasses'
}

def find_and_parse_yaml(file_handle, path: str):
    """
    Finds a specific YAML file inside the archive (ZIP or TAR.GZ) and parses it.
    The file_handle object must be either a zipfile.ZipFile or tarfile.TarFile instance.
    """
    try:
        # Check file handle type and use the correct open method
        if isinstance(file_handle, zipfile.ZipFile):
            file_data = file_handle.open(path)
        elif isinstance(file_handle, tarfile.TarFile):
            file_data = file_handle.extractfile(path)
        else:
            raise TypeError("Unsupported archive file handle type.")

        if file_data is None:
            # TarFile.extractfile returns None if the member doesn't exist
            print(f"Warning: Configuration file not found at {path}")
            return None

        with file_data:
            content = file_data.read().decode('utf-8')
            return yaml.safe_load(content)
            
    except KeyError:
        # Raised by zipfile.ZipFile.open if the file path doesn't exist
        print(f"Warning: Configuration file not found at {path}")
        return None
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
                    # Check if it's the default storage class
                    is_default = sc_yaml['metadata'].get('annotations', {}).get('storageclass.kubernetes.io/is-default-class') == 'true'
                    
                    storage_classes.append({
                        'name': name,
                        'provisioner': sc_yaml.get('provisioner', 'N/A'),
                        'default': is_default
                    })
                except KeyError as e:
                    print(f"Skipping storage class {member} due to missing key: {e}")
    
    return storage_classes

def extract_node_data(file_handle, nodes_prefix: str):
    """Extracts node information, including roles, CPU, and memory configuration."""
    node_data = []
    member_list = get_member_list(file_handle)
    
    # Iterate over all members in the archive
    for member in member_list:
        if member.startswith(nodes_prefix) and member.endswith('.yaml'):
            node_yaml = find_and_parse_yaml(file_handle, member)
            if node_yaml:
                try:
                    name = node_yaml['metadata']['name']
                    # Determine roles (master, worker, infra, etc.)
                    roles = [
                        role.replace('node-role.kubernetes.io/', '')
                        for role, value in node_yaml['metadata'].get('labels', {}).items()
                        if role.startswith('node-role.kubernetes.io/')
                    ]
                    # If no specific roles are labeled, assume generic node
                    if not roles:
                         roles = ['node']
                    
                    # Extract capacity/configuration
                    capacity = node_yaml['status'].get('capacity', {})
                    cpu = capacity.get('cpu', 'N/A')
                    memory = capacity.get('memory', 'N/A')

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
            
            # If we find the preferred prefix, return it immediately to save time
            if 'openshift-release-dev' in prefix:
                return prefix

    # If the loop completes without finding the preferred prefix, 
    # fall back to the first found candidate for compatibility.
    if candidate_prefixes:
        return candidate_prefixes[0]
        
    return ""

def generate_diagram(data: dict, output_filename="openshift_architecture.gv"):
    """
    Generates a Graphviz diagram based on the parsed OpenShift data.
    """
    dot = Digraph(comment='OpenShift Cluster Architecture', graph_attr={
        'rankdir': 'TB',  # Top to Bottom layout
        'bgcolor': '#f0f4f8', # Light background
        'fontname': 'Inter',
        'splines': 'ortho', # Straight lines
        'nodesep': '0.7', # Increased separation for better node clarity
        'ranksep': '1.0'  # Increased rank separation for better vertical flow
    })
    dot.attr('node', shape='box', style='filled', fontname='Inter')

    # --- 1. CLUSTER METADATA ---
    
    cluster_name = data['cluster_name'] or 'Unknown Cluster'
    cluster_id = data['cluster_id'] or 'N/A'
    version = data['version'] or 'N/A'
    platform = data.get('platform', 'N/A')
    
    # Fixed HTML tag syntax issue here
    metadata_label = f"""<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="4" CELLPADDING="4">
        <TR><TD COLSPAN="2" ALIGN="LEFT"><FONT POINT-SIZE="16" FACE="Inter Bold">OpenShift Cluster: {cluster_name}</FONT></TD></TR>
        <TR><TD ALIGN="LEFT">Version:</TD><TD ALIGN="LEFT">{version}</TD></TR>
        <TR><TD ALIGN="LEFT">ID:</TD><TD ALIGN="LEFT">{cluster_id}</TD></TR>
        <TR><TD ALIGN="LEFT">Platform:</TD><TD ALIGN="LEFT">{platform}</TD></TR>
    </TABLE>>"""

    dot.node('metadata', metadata_label, shape='box', style='filled', fillcolor='#e3f2fd', color='#90caf9', fontsize='12')
    
    # --- 2. CONTROL PLANE & WORKER NODES (Fixed 4-node-per-row layout) ---
    
    # Grouping nodes with explicit priority (Master > Infra > Worker) to avoid duplication
    master_nodes = [n for n in data['nodes'] if 'master' in n['roles']]
    non_master_nodes = [n for n in data['nodes'] if 'master' not in n['roles']]
    infra_nodes = [n for n in non_master_nodes if 'infra' in n['roles']]
    worker_nodes = [n for n in non_master_nodes if 'infra' not in n['roles'] and 'worker' in n['roles']]

    subgraph_order = []
    MAX_NODES_PER_ROW = 4
    
    # Helper to draw nodes with a fixed N-node-per-row limit
    def draw_nodes_with_row_limit(dot_graph: Digraph, subgraph_id: str, node_list: list, fill_color: str, color: str, row_limit: int):
        if not node_list:
            return None, None
            
        # Enforce styling within the subgraph
        # Apply fill_color/color to the nodes, not the subgraph wrapper, to avoid color conflicts
        
        # Group nodes into rows
        rows = [node_list[i:i + row_limit] for i in range(0, len(node_list), row_limit)]
        
        previous_row_first_node_id = None
        
        for i, row in enumerate(rows):
            current_row_rank_id = f"{subgraph_id}_rank_{i}"
            
            # Create a non-cluster subgraph for the current row and force nodes to be on the same rank (horizontal)
            # Use non-cluster subgraph name to prevent rendering a border/label for each row.
            with dot_graph.subgraph(name=current_row_rank_id) as row_sub:
                row_sub.attr(rank='same')
                
                # Draw nodes and link them horizontally
                for j, node in enumerate(row):
                    node_id = node['name']
                    role_display_string = ", ".join([r.capitalize() for r in node['roles']])
                    label = f"Roles: {role_display_string}\n{node['name']}\nCPU: {node['cpu']}, Mem: {node['memory']}"
                    
                    # Apply node specific attributes directly here
                    row_sub.node(node_id, label, fillcolor=fill_color, color=color) 
                    
                    # Link horizontally to force order and packing
                    if j > 0:
                        row_sub.edge(row[j-1]['name'], node_id, style='invis')
            
            # Link the first node of the previous row to the first node of the current row (vertical chain)
            current_row_first_node_id = row[0]['name']
            if previous_row_first_node_id:
                dot_graph.edge(previous_row_first_node_id, current_row_first_node_id, style='invis')
            
            previous_row_first_node_id = rows[i-1][0]['name'] # Set the first node of the current row for the next vertical link
        
        # Return the first node of the first row and the first node of the last row for global chaining
        return rows[0][0]['name'], rows[-1][0]['name'] 
        
    master_first, master_last = (None, None)
    if master_nodes:
        subgraph_order.append('cluster_masters')
        with dot.subgraph(name='cluster_masters') as sub:
            # Removed redundant total count from the label
            sub.attr(label=f'Control Plane (Masters) ({len(master_nodes)} Total)', style='filled', color='#ffcc80', fillcolor='#fff3e0', fontname='Inter Bold')
            master_first, master_last = draw_nodes_with_row_limit(dot, 'master', master_nodes, '#ffb74d', '#e65100', MAX_NODES_PER_ROW)
            
    infra_first, infra_last = (None, None)
    if infra_nodes:
        subgraph_order.append('cluster_infras')
        with dot.subgraph(name='cluster_infras') as sub:
            # Removed redundant total count from the label
            sub.attr(label=f'Infra Nodes ({len(infra_nodes)} Total)', style='filled', color='#c5cae9', fillcolor='#ede7f6', fontname='Inter Bold')
            infra_first, infra_last = draw_nodes_with_row_limit(dot, 'infra', infra_nodes, '#9fa8da', '#303f9f', MAX_NODES_PER_ROW)

    worker_first, worker_last = (None, None)
    if worker_nodes:
        subgraph_order.append('cluster_workers')
        with dot.subgraph(name='cluster_workers') as sub:
            # Removed redundant total count from the label
            sub.attr(label=f'Worker Nodes ({len(worker_nodes)} Total)', style='filled', color='#80cbc4', fillcolor='#e0f2f1', fontname='Inter Bold')
            worker_first, worker_last = draw_nodes_with_row_limit(dot, 'worker', worker_nodes, '#4db6ac', '#004d40', MAX_NODES_PER_ROW)


    # --- 3. NETWORK CONFIGURATION ---
    net_data = data['network']

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
    
    # --- 4. STORAGE CONFIGURATION (List storage classes) ---
    storage_classes = data.get('storage_classes', [])
    
    if storage_classes:
        sc_rows = ""
        for sc in storage_classes:
            default_marker = " (Default)" if sc['default'] else ""
            sc_rows += f'<TR><TD ALIGN="LEFT">{sc["name"]}{default_marker}</TD></TR>'
        
        storage_label = f"""<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="4">
            <TR><TD COLSPAN="1" BGCOLOR="#ffe0b2"><FONT POINT-SIZE="14" FACE="Inter Bold">Configured Storage Classes</FONT></TD></TR>
            {sc_rows}
        </TABLE>>"""
        dot.node('storage', storage_label, shape='plain', fontsize='12')
    else:
        storage_label = "Storage:\nNot Configured"
        dot.node('storage', storage_label, shape='cylinder', style='filled', fillcolor='#ffe0b2', color='#ff9800')

    # --- 5. INGRESS CONFIGURATION ---
    ingress_data = data['ingress']
    ingress_domain = ingress_data.get('domain', 'N/A') if ingress_data else 'N/A'

    ingress_label = f"Ingress Domain:\n{ingress_domain}"
    dot.node('ingress', ingress_label, shape='house', style='filled', fillcolor='#c8e6c9', color='#4caf50')


    # --- 6. RELATIONSHIPS / LAYOUT HINTS ---
    
    # a) Chain the node groups vertically using the last node of the previous group to the first of the next
    chain_links = []
    
    # Order of node group links
    node_group_links = [
        (master_last, infra_first),
        (infra_last, worker_first),
    ]

    # Build chain and link groups
    last_node_in_chain = None
    if master_last: last_node_in_chain = master_last
    if infra_last: last_node_in_chain = infra_last
    if worker_last: last_node_in_chain = worker_last
    
    for prev_last, next_first in node_group_links:
        if prev_last and next_first:
            dot.edge(prev_last, next_first, style='invis')


    # b) Connect Metadata to the first node group
    first_node_in_chain = master_first or infra_first or worker_first
    if first_node_in_chain:
        dot.edge('metadata', first_node_in_chain, style='invis')

    # c) Connect the last node in the chain to the Network block (Ensures Config is at the bottom)
    if last_node_in_chain:
        dot.edge(last_node_in_chain, 'network', style='invis')
    
    # d) Force Network, Storage, and Ingress to appear on the same horizontal rank at the bottom
    with dot.subgraph(name='rank_bottom_infra') as sub:
        sub.attr(rank='same')
        sub.node('network')
        sub.node('storage')
        sub.node('ingress')
    
    # e) Logical connections
    dot.edge('ingress', 'cluster_workers', label='Routes traffic to pods')
    dot.edge('network', 'cluster_masters', label='Provides service configuration')
    dot.edge('cluster_workers', 'storage', label='Accesses persistent storage')

    # Render the graph
    print(f"\nRendering diagram to {output_filename}.png and {output_filename}.pdf...")
    dot.render(output_filename, format='png', cleanup=True)
    dot.render(output_filename, format='pdf', cleanup=True)
    print("Done. Check your current directory for the generated files.")


def analyze_must_gather(must_gather_path: str):
    """
    Main function to analyze the Must Gather archive (ZIP or TAR.GZ) and generate data.
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
            # 'r:gz' mode for reading gzipped tar files
            file_handle = tarfile.open(must_gather_path, 'r:gz')
            archive_type = 'TAR.GZ'
        else:
            print("Error: Input file must be a .zip, .tar.gz, or .tgz file.")
            return

        print(f"Detected archive type: {archive_type}")
        
        # 0. Find the dynamic prefix based on the fixed version file suffix
        version_suffix = CONFIG_PATHS['version']
        prefix = get_dynamic_prefix(file_handle, version_suffix)
        
        if not prefix:
            print(f"Error: Could not find OpenShift configuration files within the archive.")
            print(f"Expected file ending in: {version_suffix}")
            return

        print(f"Detected Must Gather internal path prefix: {prefix}")
        
        # 1. Cluster Version and ID
        full_version_path = prefix + CONFIG_PATHS['version']
        version_data = find_and_parse_yaml(file_handle, full_version_path)
        if version_data:
            data['version'] = version_data.get('status', {}).get('desired', {}).get('version', 'N/A')
            data['cluster_id'] = version_data.get('spec', {}).get('clusterID', 'N/A')
            data['cluster_name'] = version_data.get('metadata', {}).get('name', 'N/A')
            if data['cluster_name'] == 'version':
                data['cluster_name'] = 'OpenShift Cluster'

        full_infra_path = prefix + CONFIG_PATHS['infrastructure']
        infra_data = find_and_parse_yaml(file_handle, full_infra_path)
        
        if infra_data:
            # Platform is usually found in status.platform for the cluster resource
            platform = infra_data.get('status', {}).get('platform', 'N/A')
            # Fallback check in spec
            if platform == 'N/A':
                 platform = infra_data.get('spec', {}).get('platform', 'N/A')
            data['platform'] = platform
    
        if data['cluster_name'] == 'OpenShift Cluster':
            data['cluster_name'] = infra_data.get('status', {}).get('infrastructureName', 'N/A')

        
        # 2. Network Configuration
        full_network_path = prefix + CONFIG_PATHS['network']
        network_data = find_and_parse_yaml(file_handle, full_network_path)
        
        network_spec = None
        if network_data:
            # Handle NetworkList case (where spec is under items[0])
            if network_data.get('kind') == 'NetworkList' and network_data.get('items') and len(network_data['items']) > 0:
                network_spec = network_data['items'][0].get('spec')
            
            # Handle single Network object case (where spec is top-level)
            elif network_data.get('spec'):
                network_spec = network_data['spec']

        if network_spec:
            data['network'] = network_spec

        # 3. Storage Configuration (Multiple Storage Classes)
        storage_base_path = prefix + CONFIG_PATHS['storage_prefix']
        print("Extracting storage class information...")
        data['storage_classes'] = extract_storage_class_data(file_handle, storage_base_path)
        print(f"Found {len(data['storage_classes'])} storage classes.")

        # 4. Ingress Configuration
        full_ingress_path = prefix + CONFIG_PATHS['ingress']
        ingress_data = find_and_parse_yaml(file_handle, full_ingress_path)
        
        ingress_spec = None
        if ingress_data:
            # Handle IngressList case (where spec is under items[0])
            if ingress_data.get('kind') == 'IngressList' and ingress_data.get('items') and len(ingress_data['items']) > 0:
                ingress_spec = ingress_data['items'][0].get('spec')
            
            # Handle single Ingress object case (where spec is top-level)
            elif ingress_data.get('spec'):
                ingress_spec = ingress_data['spec']

        if ingress_spec:
            data['ingress'] = ingress_spec
            
        # 5. Node Information
        nodes_base_path = prefix + CONFIG_PATHS['nodes_prefix']
        print("Extracting node information...")
        data['nodes'] = extract_node_data(file_handle, nodes_base_path)
        print(f"Found {len(data['nodes'])} nodes.")

        # 6. Generate Diagram
        generate_diagram(data)

    except FileNotFoundError:
        print(f"Error: Must Gather archive file not found at {must_gather_path}")
    except (zipfile.BadZipFile, tarfile.ReadError) as e:
        print(f"Error: The file {must_gather_path} is not a valid {archive_type} file. Details: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    finally:
        if file_handle:
            file_handle.close()


if __name__ == "__main__":
    # Get the file path from the user input
    must_gather_file = input("Please enter the path to the OpenShift Must Gather ZIP or TAR.GZ file: ")
    
    # Check for empty input
    if not must_gather_file:
        print("No file path provided. Exiting.")
    else:
        # Run the analysis and diagram generation
        analyze_must_gather(must_gather_file.strip())
