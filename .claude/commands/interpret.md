Interpret pipeline results for a Linear issue and post the interpretation.

Look for pending interpretation requests in $LR_RESULTS_MOUNT (default: /mnt/lr-results). Each request is a `.lr-interpret` JSON file inside an issue directory.

If `$ARGUMENTS` is provided, use it as the issue ID. Otherwise, find any pending `.lr-interpret` files.

Steps:

1. Read the `.lr-interpret` JSON file from `$LR_RESULTS_MOUNT/$ISSUE_ID/.lr-interpret`
2. Extract metadata: issue_id, timestamp, command, exit_code, duration, log_tail
3. Read result files from `$LR_RESULTS_MOUNT/$ISSUE_ID/$TIMESTAMP/`:
   - List all files
   - Read contents of small text files (*.csv, *.json, *.txt, *.tsv under 50KB)
   - Look at any images (*.png, *.jpg)
   - Read `.lr-agent-prompt` if present for custom instructions
4. Write a concise interpretation covering:
   - What completed
   - Key quantitative results
   - Notable observations
   - Suggested next steps
5. Post the interpretation to Linear:
   - Run: `lr-update comment "$ISSUE_ID" "**Agent Interpretation**\n\n$INTERPRETATION"`
   - Run: `lr-update status "$ISSUE_ID" "Ready for Review"`
6. Remove the `.lr-interpret` file to mark as handled
