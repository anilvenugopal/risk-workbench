# Cat Modeling Workflow Tool — clickable mock

A high-fidelity, runnable mock of the application shell and primary flows, on the
real CSS framework with real HTMX + Alpine. See `MOCK_NOTES.md` for what is real
vs faked and the API contract sketch.

## Run

```bash
python serve.py     # http://localhost:8000  (Python 3, no dependencies)
```

## Try
- Home — cards + tables, no sidebar (rail shows Home active).
- Submissions — pick a row → file inventory (tags, changed/missing, discrepancies)
  + the submission's workflows. Filter by Customer/Program.
- Open a workflow → click "Run workflow" → watch stages progress live, then complete.
- Open a workflow *from a submission* → notice rail + breadcrumb switch to Workflows.
- Ctrl/Cmd-J → global search (navigation, submissions, workflows, templates).
- /signin — SSO button + backdoor user dropdown.
