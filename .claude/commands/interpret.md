Interpret pipeline results for a Linear issue and post the interpretation.

Run the Python interpret module:

```bash
python -m lrlib.interpret $ARGUMENTS
```

This reads the `.lr-interpret` request from `$LR_RESULTS_MOUNT/$ISSUE_ID/`, interprets the results using the Claude Agent SDK, posts the interpretation to Linear, and cleans up the request file.

Use `--host` flag for context-free interpretation (inlines file contents). Without `--host`, runs in guest mode with full tool access.
