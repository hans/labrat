# lr — Linear-integrated Snakemake executor

Runs Snakemake on a remote server via SSH, copies results back, updates Linear, and optionally queues Claude Code interpretation.

| Script | Runs on | Purpose |
|---|---|---|
| `lr` | Host | SSH to remote, SCP results, update Linear, write `.lr-interpret` |
| `lr-update` | Anywhere | Linear API wrapper (create/update issues, post comments) |
| `interpret` | Guest (Docker) | Read `.lr-interpret`, invoke Claude Code, post interpretation to Linear |

## Setup

**`~/.lr/config`:**

```bash
LR_REMOTE_HOST="labserver"
LINEAR_API_KEY="lin_api_xxxxx"
LINEAR_TEAM_ID="your-team-uuid"
LR_RESULTS_DIR="$HOME/.lr/results"
LR_GUEST_MOUNT="/path/to/shared"     # host path mounted into docker guest
LR_NTFY_TOPIC=""                     # optional, ntfy on failure
```

**Install:**

```bash
cp lr lr-update ~/bin/               # host
scp lr-update remote:~/bin/          # remote (needs its own ~/.lr/config)
```

**Remote directory convention:**

```
/data/project/analysis/
├── Snakefile
├── .lr-results            # glob patterns for files to SCP back (default: figures/* results/*)
├── .lr-agent-prompt       # custom instructions for Claude interpretation
```

## Usage

```bash
lr --issue JON-15 --remote-dir /data/barakeet/ganong --interpret snakemake -j4
lr --project Barakeet snakemake -j4     # interactive issue selection
LINEAR_ISSUE=JON-15 lr snakemake -j4    # issue from env
```

**What `lr` does:**

1. Resolves Linear issue (from `--issue`, `$LINEAR_ISSUE`, or interactive picker)
2. Sets status → "Computing"
3. SSHs to remote, runs command, streams output
4. SCPs results back to `$LR_RESULTS_DIR/<issue>/<timestamp>/`
5. Copies results to guest mount
6. Updates Linear (Done/Failed + comment with log tail)
7. If `--interpret`: writes `.lr-interpret` to guest mount

**Interpretation** — from Claude Code on the guest:

```
/interpret <ISSUE_ID>
```

Reads result files, generates interpretation, posts to Linear, sets status to "Ready for Review".

**`lr-update` commands:**

```bash
lr-update done JON-15 "Pipeline completed"
lr-update fail JON-15 "OOM at step 3"
lr-update comment JON-15 "Looks good"
lr-update status JON-15 "Computing"
lr-update new "Analysis title" --project Barakeet
lr-update list --project Barakeet --status "In Progress"
```

## Tests

Requires Docker and `~/.lr/config` with Linear credentials.

```bash
uv sync --group dev
uv run pytest tests/ -v
```

Tests spin up a Docker container as a mock remote (sshd + Snakemake), run an Iris classifier pipeline, and verify the full flow: SCP, guest mount, `.lr-interpret` handoff, `interpret` script execution (with mock `claude`), and Linear status updates.
