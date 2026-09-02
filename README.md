# OpenShift Must-Gather Visualizer

A Python utility that transforms OpenShift `must-gather` archives into architectural diagrams. It parses cluster versions, node roles, networking CIDRs, storage classes, and related metadata.

## Features
* **Multi-format support**: `.zip`, `.tar`, `.tar.gz`, `.tgz`, and extracted directories
* **Dynamic path discovery**: Finds the `openshift-release-dev` prefix inside the archive
* **Exclusive role grouping**: Control plane, infra, worker, other (no double-counting)
* **Red Hat brand styling**: Colours from [brand.redhat.com](https://www.redhat.com/en/about/brand/standards) (`rh_brand.py`)
* **Graphviz architecture diagram**: equal-width panels with dense per-node metadata
* **Namespace topology (draft)**: Console Developer-like view for one project (`-n`), from must-gather or `oc adm inspect`
* **MCP server**: expose diagram tools to Claude Code, Cursor, or any MCP client

## Prerequisites
1. **Python 3.10+**
2. **Graphviz** (system package)
   * Fedora/RHEL: `sudo dnf install graphviz`
   * macOS: `brew install graphviz`
   * Ubuntu/Debian: `sudo apt-get install graphviz`
3. **Red Hat fonts** (optional): [RedHatOfficial/RedHatFont](https://github.com/RedHatOfficial/RedHatFont) -- diagrams fall back to Helvetica

## Installation

### Option 1: uv tool install (recommended)

Installs `archdiag-mcp` as a standalone tool. No clone, no venv, no PYTHONPATH needed.

```bash
uv tool install git+https://github.com/AnneFaulhaber/archdiag.git
```

Update:
```bash
uv tool upgrade archdiag
```

### Option 2: Clone and venv (for development)

```bash
git clone https://github.com/AnneFaulhaber/archdiag.git
cd archdiag
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Collect a must-gather
```bash
oc adm must-gather --dest-dir=./my-cluster-data
# then archive the directory, or point the scripts at an existing .tar.gz
```

## MCP server

### Register with Claude Code
```bash
# After uv tool install:
claude mcp add -s user archdiag -- archdiag-mcp

# After clone + venv:
claude mcp add -s user archdiag -- /path/to/archdiag/.venv/bin/python -m archdiag.mcp_server
```

### Register with Cursor
After `uv tool install`, update `.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "archdiag": {
      "command": "archdiag-mcp",
      "args": []
    }
  }
}
```

### Tools
| Tool | Purpose |
|------|---------|
| `list_namespaces` | Discover namespaces in an archive / inspect tree |
| `analyze_cluster` | Must-gather cluster metadata (JSON) |
| `generate_cluster_diagram` | Cluster architecture PNG (+ PDF) |
| `analyze_namespace_topology` | Namespace topology model (JSON) |
| `generate_namespace_diagram` | Namespace topology PNG |

## CLI usage

```bash
# After uv tool install, the entry point is archdiag-mcp (MCP stdio server).
# For direct CLI diagram generation, use the package modules:
python -m archdiag.openshift_diagram_standard /path/to/must-gather.tar.gz
python -m archdiag.openshift_diagram_standard /path/to/must-gather.tar.gz -o my-cluster

# Namespace topology (draft)
python -m archdiag.openshift_diagram_namespace ./inspect-myapp -n myapp
python -m archdiag.openshift_diagram_namespace must-gather.tar.gz -n openshift-monitoring -o mon-topo
```

`--namespace` / `-n` is required for namespace diagrams.

## Project structure

```
archdiag/                  # Python package
  __init__.py
  mcp_server.py            # MCP server entry point
  openshift_mg.py          # Must-gather parser
  openshift_ns.py          # Namespace-scoped parser
  openshift_diagram_standard.py  # Graphviz cluster diagram
  openshift_diagram_namespace.py # Namespace topology diagram
  rh_brand.py              # Red Hat brand tokens
pyproject.toml             # Package metadata and dependencies
requirements.txt           # For venv-based development
```

## Output files
* `openshift_architecture.png` / `.pdf` -- Graphviz cluster diagram
* `openshift_ns_<namespace>.png` -- Namespace topology (draft)
