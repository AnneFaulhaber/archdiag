# OpenShift Must-Gather Visualizer

A Python utility that transforms OpenShift `must-gather` archives into architectural diagrams. It automatically parses cluster versions, node roles, networking CIDRs, and storage classes.

## Features
* **Multi-format Support**: Works with `.zip`, `.tar`, `.tar.gz`, and `.tgz` archives.
* **Dynamic Path Discovery**: Automatically finds the `openshift-release-dev` pathing inside the bundle.
* **Visual Architecture**: Generates a professional diagram using Kubernetes-standard icons(https://diagrams.mingrammer.com/) or graphiz-native rendering.
* **Resource Parsing**: Extracts CPU/Memory (normalized to GiB), Network Types, and Storage Class defaults.

## Prerequisites
1.  **Python 3.8+**
2.  **Graphviz**: The system-level engine for drawing diagrams.
    * *macOS*: `brew install graphviz`
    * *Ubuntu/Debian*: `sudo apt-get install graphviz`
    * *Windows*: `choco install graphviz`

## Installation
Clone this repository and install the dependencies:
```bash
git clone <your-repo-url>
cd <your-repo-name>
pip install -r requirements.txt
```

## How to use

### 1. Collect your Must-Gather
Run the standard OpenShift command to collect cluster data:

```bash
oc adm must-gather --dest-dir=./my-cluster-data
```

### Running the Script
Run the script and follow the interactive prompt:

```bash
python openshift_diagram_standard.py
```

or

```bash
python openshift_diagram_icons.py
```


### Execution Steps
1. When prompted, provide the full path to your archive: Please enter the path to the OpenShift Must Gather ZIP or TAR.GZ/TAR file: /path/to/must-gather.tar.gz
1. The script will detect the internal path prefix and parse the YAML files.
1. The script will output progress to the console, identifying the number of nodes and storage classes found.

### Output Files
* openshift_k8s_icons.png: A visual diagram using Kubernetes icons.
* openshift_architecture.gv.png / .pdf: (Optional) A Graphviz-native rendering of the cluster state.