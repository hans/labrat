# lr — Linear-integrated Snakemake executor

`lr` runs Snakemake pipelines on a remote server via SSH, copies results back to your local machine, updates Linear issues with status and logs, and optionally calls Claude to interpret the results.

There are two scripts:

| Script | Where it runs | Purpose |
|---|---|---|
| `lr` | Local host only | Wraps remote Snakemake execution. SCPs results. Updates Linear. Optionally runs Claude interpretation. |
| `lr-update` | Everywhere (host, guest, remote) | Standalone Linear API wrapper for creating/updating issues and posting comments. |

## Setup

### 1. Configure `~/.lr/config`

Create `~/.lr/config` with your credentials and paths:

```bash
# SSH
LR_REMOTE_HOST="labserver"           # SSH config host name
LR_REMOTE_USER=""                    # optional, if not in SSH config

# Linear
LINEAR_API_KEY="lin_api_xxxxx"
LINEAR_TEAM_ID="your-team-uuid"

# Paths
LR_RESULTS_DIR="$HOME/.lr/results"   # where SCP'd results land on host
LR_GUEST_MOUNT="/path/to/shared"     # host path mounted into docker guest

# Agent (for --interpret mode)
ANTHROPIC_API_KEY=""                  # for calling Claude API directly

# Notifications
LR_NTFY_TOPIC=""                     # optional, sends ntfy on failure
```

### 2. Install the scripts

```bash
# On your local machine
cp lr lr-update ~/bin/
chmod +x ~/bin/lr ~/bin/lr-update

# On the remote server (lr-update only)
scp lr-update remote:~/bin/
ssh remote 'chmod +x ~/bin/lr-update'
```

The remote also needs its own `~/.lr/config` with `LINEAR_API_KEY` and `LINEAR_TEAM_ID`.

### 3. Set up your remote analysis directory

Each analysis directory on the remote should contain a Snakefile and can optionally include:

```
/data/project/analysis/
├── Snakefile
├── config.yaml
├── .lr-results            # glob patterns for files to SCP back
├── .lr-agent-prompt       # custom instructions for Claude interpretation
├── figures/
├── results/
└── logs/
```

**`.lr-results`** — one glob pattern per line specifying which files to copy back:

```
figures/*.png
results/*.json
results/*.csv
```

If `.lr-results` doesn't exist, `lr` defaults to copying `figures/*` and `results/*`.

**`.lr-agent-prompt`** — free-text instructions that get passed to Claude when `--interpret` is used:

```
This is an Iris dataset classification analysis using a Random Forest model.
Summarize model performance, note any misclassified species, and suggest improvements.
```

## Usage

### Running a pipeline with `lr`

```bash
lr [options] <command...>
```

**Options:**

| Flag | Description |
|---|---|
| `--issue ID` | Linear issue identifier (e.g. `JON-15`) |
| `--remote-dir DIR` | Working directory on remote server |
| `--project NAME` | Linear project name (for interactive selection / new issues) |
| `--interpret` | Run Claude interpretation on results after completion |

**Examples:**

```bash
# Run snakemake, link to a specific Linear issue
lr --issue JON-15 --remote-dir /data/barakeet/ganong snakemake -j4

# Run without specifying an issue (interactive picker)
lr --remote-dir /data/barakeet/ganong snakemake -j4
#   No issue specified. Fetching open issues...
#
#     1. JON-12  Multivariate gradient analysis     [Computing]
#     2. JON-15  Ganong population decoding          [In Progress]
#     3. JON-18  Acoustic neurometrics               [Todo]
#   Select issue [1-3] or 'n' to create new:

# Use an environment variable for the issue
LINEAR_ISSUE=JON-15 lr --remote-dir /data/barakeet/ganong snakemake -j4

# Run with Claude interpretation of results
lr --issue JON-15 --remote-dir /data/barakeet/ganong --interpret snakemake -j4

# Filter interactive selection by project
lr --project Barakeet --remote-dir /data/barakeet/ganong snakemake -j4
```

### What happens when you run `lr`

1. **Resolves the Linear issue** — from `--issue`, `$LINEAR_ISSUE`, or interactive selection
2. **Sets issue status to "Computing"** in Linear
3. **SSHs to the remote** and runs your command, streaming stdout/stderr to your terminal and saving a log
4. **SCPs result files** from the remote back to `$LR_RESULTS_DIR/<issue-id>/<timestamp>/`
5. **Copies results to the guest mount** (if `LR_GUEST_MOUNT` is set) so Claude Code in Docker can access them
6. **Updates Linear** — posts a comment with the command, duration, and log tail; sets status to "Done" on success or posts a failure comment on error
7. **Interprets results** (if `--interpret`) — sends result files and logs to Claude, posts the interpretation as a Linear comment, and sets status to "Ready for Review"

### Using `lr-update` standalone

`lr-update` is a thin wrapper around Linear's GraphQL API. It works anywhere with network access and a valid `~/.lr/config`.

```bash
# Mark an issue as done with a message
lr-update done JON-15 "Pipeline completed successfully"

# Mark an issue as failed
lr-update fail JON-15 "OOM at step 3"

# Post a comment
lr-update comment JON-15 "Intermediate results look good"

# Change issue status
lr-update status JON-15 "Computing"

# Create a new issue
lr-update new "Ganong population decoding" --project Barakeet --status Computing

# Create a new issue with a description
lr-update new "New analysis" --project Barakeet --description "Replicate Fig 3 with restricted peaks"

# Create a new project
lr-update new-project "Paper Name" --description "Neural encoding study"

# List issues (optionally filtered)
lr-update list
lr-update list --project Barakeet
lr-update list --project Barakeet --status "In Progress"

# List all projects
lr-update list-projects
```

### Using `lr-update` on the remote server

#### In Jupyter notebooks

```python
import subprocess

def lr(cmd, *args):
    subprocess.run(["lr-update", cmd, *args], check=False)

# After a long cell completes
lr("comment", "JON-15", "Inspected population results. 6/10 subjects significant.")
lr("status", "JON-15", "Ready for Review")
```

#### In tmux sessions

```bash
lr-update comment JON-15 "Kicked off reanalysis with restricted peaks"
lr-update status JON-15 "Computing"
```

#### In Snakemake hooks (for jobs not launched via `lr`)

```python
import os
LINEAR_ISSUE = os.environ.get("LINEAR_ISSUE", "")

onsuccess:
    if LINEAR_ISSUE:
        shell(f"lr-update done {LINEAR_ISSUE} 'Pipeline complete.'")

onerror:
    if LINEAR_ISSUE:
        shell(f"lr-update fail {LINEAR_ISSUE} 'Pipeline failed. See log: {{log}}'")
```

### Using results from Claude Code (in Docker guest)

Claude Code runs in a Docker container and can read results placed by `lr` into the guest mount:

```bash
# See what results are available
ls /mnt/lr-results/

# View results for a specific issue
ls /mnt/lr-results/JON-15/

# Post an update after reviewing results
lr-update comment JON-15 "Interpretation of results: ..."
lr-update done JON-15 "Analysis complete. Key findings: ..."
```

## Running tests

The integration tests spin up a Docker container that acts as a mock remote server (with sshd and Snakemake), a mock Anthropic API server, and use your real Linear credentials to create/update test issues.

### Prerequisites

- Docker running locally
- `~/.lr/config` with valid `LINEAR_API_KEY` and `LINEAR_TEAM_ID`
- Python dev dependencies installed

```bash
# Install dev dependencies
uv sync --group dev

# Run the integration tests
uv run pytest tests/test_integration.py -v
```

### What the tests cover

**`test_successful_pipeline_with_interpret`** — Full happy path: creates a test issue in Linear, runs a Snakemake pipeline (Iris dataset Random Forest classifier) on the Docker "remote", verifies results are SCP'd back (metrics.json, predictions.csv, confusion_matrix.png, pca_scatter.png), checks that metrics.json has >80% accuracy, confirms results appear in the guest mount, verifies the Anthropic mock was called for interpretation, and checks the issue ends up in "Ready for Review" status.

**`test_successful_pipeline_no_interpret`** — Same as above but without `--interpret`: verifies results are SCP'd, no Anthropic API call is made, and the issue ends up in "Done" status.

**`test_failed_pipeline`** — Runs Snakemake with a nonexistent target so it fails: verifies `lr` itself exits 0 (it reports the failure rather than propagating it), no Anthropic call is made (interpretation is skipped on failure), and the output contains a failure indication.

### Test infrastructure

The test suite uses:
- A Docker container (`tests/docker/Dockerfile`) running sshd with an ephemeral SSH keypair, with Snakemake and scientific Python packages installed
- A sample Snakemake pipeline (`tests/docker/Snakefile`) that trains a Random Forest on the Iris dataset and produces metrics, predictions, and plots
- A local HTTP server mocking the Anthropic API that returns a canned interpretation response
- Real Linear API calls (test issues are created with an `lr-integration-test` prefix)
