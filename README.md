# OpenShift Must-Gather Visualizer

A Python utility that transforms OpenShift `must-gather` archives into architectural diagrams. It parses cluster versions, node roles, networking CIDRs, storage classes, and related metadata.

## Features
* **Multi-format support**: `.zip`, `.tar`, `.tar.gz`, `.tgz`
* **Dynamic path discovery**: Finds the `openshift-release-dev` prefix inside the archive
* **Exclusive role grouping**: Control plane → infra → worker → other (no double-counting)
* **Red Hat brand styling**: Colours from [brand.redhat.com](https://www.redhat.com/en/about/brand/standards) (`rh_brand.py`)
* **Graphviz architecture diagram**: equal-width panels with dense per-node metadata
* **Namespace topology (draft)**: Console Developer–like view for one project (`-n`), from must-gather or `oc adm inspect`
* **MCP server**: expose diagram tools to Cursor / Claude Desktop (`mcp_server.py`)
* **Rich metadata**: CPU/memory (normalized), Ready status, IPs, API/console URLs, topology, MTU, storage provisioners

## Prerequisites
1. **Python 3.11+** (3.8+ for CLI scripts alone)
2. **Graphviz** (system package)
   * macOS: `brew install graphviz`
   * Ubuntu/Debian: `sudo apt-get install graphviz`
   * Windows: `choco install graphviz`
3. **Red Hat fonts** (optional): [RedHatOfficial/RedHatFont](https://github.com/RedHatOfficial/RedHatFont) — diagrams fall back to Helvetica

## Installation
```bash
git clone <your-repo-url>
cd <your-repo-name>
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Collect a must-gather
```bash
oc adm must-gather --dest-dir=./my-cluster-data
# then archive the directory, or point the scripts at an existing .tar.gz
```

## Usage

### Graphviz architecture
```bash
python openshift_diagram_standard.py /path/to/must-gather.tar.gz

# Custom output prefix
python openshift_diagram_standard.py /path/to/must-gather.tar.gz -o my-cluster
```

### Namespace topology (draft)
Console Developer–like view of one project, using [mingrammer](https://diagrams.mingrammer.com/docs/getting-started/examples) Kubernetes patterns (exposed Deployment replicas + StatefulSet storage).

```bash
# Prefer a focused inspect of the project
oc adm inspect ns/myapp --dest-dir=./inspect-myapp

python openshift_diagram_namespace.py ./inspect-myapp -n myapp
python openshift_diagram_namespace.py must-gather.tar.gz -n openshift-monitoring -o mon-topo
```

`--namespace` / `-n` is required. Parser: `openshift_ns.py`.

If you omit the archive path, the cluster diagram script prompts interactively.

### MCP server (Cursor)
Project config is in `.cursor/mcp.json`. After install, enable the **archdiag** server in Cursor MCP settings (or restart Cursor).

Tools:
| Tool | Purpose |
|------|---------|
| `list_namespaces` | Discover namespaces in an archive / inspect tree |
| `analyze_cluster` | Must-gather cluster metadata (JSON) |
| `generate_cluster_diagram` | Cluster architecture PNG (+ PDF) |
| `analyze_namespace_topology` | Namespace topology model (JSON) |
| `generate_namespace_diagram` | Namespace topology PNG |

Manual stdio run:
```bash
source .venv/bin/activate
PYTHONPATH=. python mcp_server.py
```

Shared parsing: `openshift_mg.py`  
Brand tokens: `rh_brand.py` (nodes = teal family; network/storage/ingress = gray + interaction-blue)

## Output files
* `openshift_architecture.png` / `.pdf` — Graphviz cluster diagram
* `openshift_ns_<namespace>.png` — Namespace topology (draft)
