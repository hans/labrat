# `lr` — Linear-integrated snakemake executor

## Execution environment

Three nodes:

```
┌─────────────────────────────────────────┐
│  LOCAL HOST                             │
│  Your macOS machine                     │
│                                         │
│  - lr command lives here                │
│  - SSH access to remote                 │
│  - VSCode client (UI)                   │
│  - Can curl Linear API, Anthropic API   │
│  - Receives SCP'd results from remote   │
│  - Passes results into local guest      │
│                                         │
│  ┌───────────────────────────────────┐  │
│  │  LOCAL GUEST                      │  │
│  │  Docker container (sandboxed)     │  │
│  │                                   │  │
│  │  - Claude Code runs here          │  │
│  │  - VSCode server                  │  │
│  │  - Restricted mounts from host    │  │
│  │  - Network access (Linear API,    │  │
│  │    Anthropic API) but NO SSH      │  │
│  │  - Can read results placed in     │  │
│  │    mounted directory by host      │  │
│  └───────────────────────────────────┘  │
└─────────────┬───────────────────────────┘
              │ SSH
              ▼
┌─────────────────────────────────────────┐
│  REMOTE                                 │
│  Linux analysis server (university)     │
│                                         │
│  - Snakemake pipelines                  │
│  - Heavy compute                        │
│  - No agents                            │
│  - Can curl Linear API                  │
│  - Stores raw data, figures, results    │
└─────────────────────────────────────────┘
```

Key constraints:
- `lr` runs on **local host** only (not in the container, not on remote)
- Claude Code (local guest) cannot SSH to the remote
- Results must be SCP'd to host, then made available to guest via mount
- The remote runs snakemake; that's it. No agent, no interpretation.
- Linear issue ID is resolved locally (env var, arg, or agent inference)

---

## What `lr` does

```bash
lr [--issue JON-15] [--remote-dir /data/barakeet/ganong] [--interpret] \
   snakemake -j4 --configfile config.yaml
```

1. Resolve the Linear issue (see resolution below)
2. Update issue status → "Computing"
3. SSH to remote, run the snakemake command
4. Stream stdout/stderr back to local terminal
5. When command exits:
   a. SCP result files from remote to local staging dir
   b. Copy results into guest-mounted directory
   c. Update Linear issue based on exit code
   d. If `--interpret`: invoke Claude (via API or Claude Code) to
      read the results and post a rich interpretation to Linear

---

## Issue resolution

In order:

1. `--issue JON-15` explicit flag → use it
2. `$LINEAR_ISSUE` env var → use it
3. If neither: query Linear for open issues in the relevant project,
   display them, ask user to pick interactively

The "agent inference" option: if issue can't be resolved and `--interpret`
is set, the agent can be asked to determine the right issue based on the
command being run and the project context. But this is a stretch goal —
start with interactive selection.

Project resolution (needed for agent inference and new issue creation):
1. `--project Barakeet` explicit flag
2. `$LINEAR_PROJECT` env var
3. Interactive selection from existing projects

---

## Config

`~/.lr/config` (bash-sourceable):

```bash
# SSH
LR_REMOTE_HOST="labserver"           # SSH config host name
LR_REMOTE_USER=""                    # optional, if not in SSH config

# Linear
LINEAR_API_KEY="lin_api_xxxxx"
LINEAR_TEAM_ID="499cfc52-16bb-4ff0-9fa3-b32c19347357"

# Paths
LR_RESULTS_DIR="$HOME/.lr/results"   # where SCP'd results land on host
LR_GUEST_MOUNT="/path/to/shared"     # host path mounted into docker guest

# Agent (for --interpret mode)
LR_GUEST_CONTAINER=""                # docker container name for auto-trigger (optional)

# Notifications
LR_NTFY_TOPIC=""                     # optional, failures only
```

---

## Directory conventions on remote

Each analysis directory on the remote can have:

```
/data/barakeet/ganong_decoding/
├── Snakefile
├── config.yaml
├── .lr-results            # glob patterns for files to SCP back
├── .lr-agent-prompt       # custom interpretation instructions (optional)
├── figures/               # generated plots
├── results/               # generated data
└── logs/                  # snakemake logs
```

`.lr-results` (line-separated glob patterns):
```
figures/*.pdf
figures/*.png
results/*.csv
results/*.json
logs/snakemake.log
```

If `.lr-results` doesn't exist, default to:
```
figures/*
results/*
```

---

## Execution flow in detail

### Step 1: Resolve issue

```bash
# Explicit
lr --issue JON-15 snakemake -j4

# From env
LINEAR_ISSUE=JON-15 lr snakemake -j4

# Interactive (no issue specified)
lr snakemake -j4
# Output:
#   No issue specified. Open issues in Barakeet:
#     1. JON-12  Multivariate gradient analysis     [Computing]
#     2. JON-15  Ganong population decoding          [In Progress]
#     3. JON-18  Acoustic neurometrics               [Todo]
#   Select issue [1-3] or 'n' to create new:
```

### Step 2: Update Linear status → Computing

```bash
curl -s -X POST https://api.linear.app/graphql \
  -H "Authorization: $LINEAR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "mutation { issueUpdate(id: \"ISSUE_UUID\", input: { stateId: \"COMPUTING_STATE_ID\" }) { success } }"}'
```

### Step 3: SSH + run snakemake

```bash
ssh $LR_REMOTE_HOST "cd $REMOTE_DIR && snakemake -j4 --configfile config.yaml" 2>&1 | tee "$LR_RESULTS_DIR/$ISSUE_ID/run.log"
EXIT_CODE=${PIPESTATUS[0]}
```

stdout/stderr stream to the local terminal in real time AND get saved to a log file.

### Step 4: SCP results

```bash
# Read .lr-results from remote (or use defaults)
PATTERNS=$(ssh $LR_REMOTE_HOST "cat $REMOTE_DIR/.lr-results 2>/dev/null" || echo -e "figures/*\nresults/*")

TIMESTAMP=$(date +%Y%m%dT%H%M%S)
LOCAL_DEST="$LR_RESULTS_DIR/$ISSUE_ID/$TIMESTAMP"
mkdir -p "$LOCAL_DEST"

# SCP each pattern
while IFS= read -r pattern; do
  scp -r "$LR_REMOTE_HOST:$REMOTE_DIR/$pattern" "$LOCAL_DEST/" 2>/dev/null || true
done <<< "$PATTERNS"

# Also grab .lr-agent-prompt if it exists
scp "$LR_REMOTE_HOST:$REMOTE_DIR/.lr-agent-prompt" "$LOCAL_DEST/" 2>/dev/null || true
```

### Step 5: Copy to guest mount

```bash
# Make results available to Claude Code in the docker container
if [ -n "$LR_GUEST_MOUNT" ]; then
  cp -r "$LOCAL_DEST" "$LR_GUEST_MOUNT/lr-results/$ISSUE_ID/"
fi
```

After this, Claude Code can see the results at whatever path the mount
maps to inside the container (e.g., `/mnt/lr-results/JON-15/20260321T152500/`).

### Step 6: Update Linear

On success (exit 0):
```
lr-update done $ISSUE_ID "Snakemake pipeline completed successfully.

Command: snakemake -j4 --configfile config.yaml
Duration: 12m 34s
Results SCP'd to: $LOCAL_DEST

$(tail -20 $LOCAL_DEST/../run.log)"
```

On failure (exit != 0):
```
lr-update fail $ISSUE_ID "Snakemake pipeline failed (exit code $EXIT_CODE).

Command: snakemake -j4 --configfile config.yaml
Duration: 4m 12s

Last 20 lines of output:
$(tail -20 $LOCAL_DEST/../run.log)"
```

Also send ntfy on failure if configured.

### Step 7 (optional): Agent interpretation via Claude Code

If `--interpret` flag was set:

1. Write a `.lr-interpret` JSON request to the guest mount:

```bash
cat > "$LR_GUEST_MOUNT/lr-results/$ISSUE_ID/.lr-interpret" <<EOF
{
  "issue_id": "JON-15",
  "timestamp": "20260321T152500",
  "command": "snakemake -j4 --configfile config.yaml",
  "exit_code": 0,
  "duration": "12m 34s",
  "results_dir": "20260321T152500",
  "log_tail": "<last 30 lines>"
}
EOF
```

2. If `LR_GUEST_CONTAINER` is set, auto-trigger via docker exec:

```bash
docker exec "$LR_GUEST_CONTAINER" interpret JON-15
```

Otherwise, print a message for the user to run `interpret JON-15` manually
in Claude Code.

3. The `interpret` script (running in the guest) reads the request,
   assembles context (metadata, file listings, small file contents,
   `.lr-agent-prompt`), invokes `claude -p` for interpretation, then
   posts the result as a Linear comment via `lr-update` and sets the
   issue status to "Ready for Review".

This approach lets Claude Code use its full tool suite (reading images,
running follow-up analysis) rather than being limited to a single
stateless API call. It also removes the need for `ANTHROPIC_API_KEY`
in `~/.lr/config`.

---

## `lr-update` — standalone Linear updater

For use on its own (without wrapping a command) or from the remote server.
Lives on both host and remote.

```bash
# Post a success update
lr-update done JON-15 "Message"

# Post a failure update
lr-update fail JON-15 "Error description"

# Post a comment
lr-update comment JON-15 "Some note"

# Change status
lr-update status JON-15 "Ready for Review"

# Create a new issue
lr-update new "Title" --project Barakeet [--status Computing] [--description "..."]
# Output: Created JON-22

# Create a new project
lr-update new-project "Paper Name" [--description "..."]

# List issues
lr-update list [--project Barakeet] [--status "Ready for Review"]

# List projects
lr-update list-projects
```

This is just a thin bash wrapper around Linear's GraphQL API.
Implementation: one function per command, each constructs a GraphQL
query/mutation and curls `https://api.linear.app/graphql`.

---

## Claude Code integration

Claude Code runs in the local guest (docker container). It can:
- Read results from the guest mount (`/mnt/lr-results/JON-15/...`)
- Call the Linear API directly (has network access)
- Call `lr-update` if it's installed in the container

It cannot:
- SSH to the remote
- Run `lr` (that's a host-only command)

### CLAUDE.md instructions for Claude Code

```markdown
## Linear integration

Results from remote compute jobs are placed in /mnt/lr-results/<issue-id>/
by the lr wrapper running on the host.

### Reading results
Check /mnt/lr-results/ for available results:
  ls /mnt/lr-results/

View results for a specific issue:
  ls /mnt/lr-results/JON-15/

### Updating Linear
Use lr-update to post updates:
  lr-update comment JON-15 "Interpretation of results: ..."
  lr-update done JON-15 "Analysis complete. Key findings: ..."
  lr-update new "New analysis strand" --project Barakeet

### Creating new issues
When starting a new analysis direction:
  lr-update new "Title" --project "Barakeet" --description "What and why"
  # Note the returned issue ID (e.g., JON-25)

### Associating with projects
- Barakeet: speech/ECoG, categorical perception, /d/-/n/, ganong, STG
- Ideal / Neural Footprints: encoding models, RSA, decoding comparison
- If unsure: lr-update list-projects
```

---

## Remote server integration

The remote only needs `lr-update` installed (for standalone use in
notebooks and tmux sessions). `lr` itself never runs on the remote.

### Setup on remote

```bash
# Install lr-update
cp lr-update ~/bin/
chmod +x ~/bin/lr-update

# Configure
cat > ~/.lr/config << 'EOF'
LINEAR_API_KEY="lin_api_xxxxx"
LINEAR_TEAM_ID="499cfc52-16bb-4ff0-9fa3-b32c19347357"
EOF
```

### Notebook usage (on remote)

```python
import subprocess
def lr(cmd, *args):
    subprocess.run(["lr-update", cmd, *args], check=False)

# After a long cell:
lr("comment", "JON-15", "Inspected population results. 6/10 subjects significant.")
lr("status", "JON-15", "Ready for Review")
```

### Manual tmux usage (on remote)

```bash
lr-update comment JON-15 "Kicked off reanalysis with restricted peaks"
lr-update status JON-15 "Computing"
```

### Snakemake hooks (on remote, for jobs NOT launched via `lr`)

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

---

## Build order

1. **`lr-update`**: the Linear GraphQL wrapper. Commands: done, fail,
   comment, status, new, new-project, list, list-projects.
   Test on both host and remote.

2. **`lr` basic**: SSH to remote, run snakemake, capture exit code,
   call lr-update. No SCP, no agent. Just "run and report."

3. **`lr` with SCP**: add .lr-results support, SCP files back,
   copy to guest mount.

4. **`lr` with `--interpret`**: add Anthropic API call for agent
   interpretation of results. Post interpretation as Linear comment.

5. **Interactive issue selection**: when no issue specified, query
   Linear and let user pick.

---

## Script summary

Three scripts total:

| Script | Where | What |
|--------|-------|------|
| `lr` | local host only | Wraps snakemake via SSH. SCPs results. Writes interpret request. Updates Linear. |
| `lr-update` | everywhere (host, guest, remote) | Standalone Linear API wrapper. Creates/updates issues and comments. |
| `interpret` | local guest only | Reads interpret request, invokes `claude -p`, posts interpretation to Linear. |

All are bash. All source `~/.lr/config`. Total: ~280 lines for lr, ~150
lines for lr-update, ~120 lines for interpret.
