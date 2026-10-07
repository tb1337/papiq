# Evaluation set

Synthetic German documents (no real personal data) with the metadata expected of them. They run
through the same classification and attribute extraction steps as the pipeline, to compare
language models and to check that the checks catch wrong answers.

- `master_data.json`: contacts, document types, tags and attributes the documents are matched
  against.
- `cases/<name>.md`: the document text, as the parse step would produce it (OCR and parsing are
  not part of the evaluation).
- `cases/<name>.json`: `expected` (lane and fields) and `fake`, a fixed answer for runs without a
  model. Some fixed answers are wrong on purpose (`fake.lane` then names the lane they must lead
  to); two cases carry instructions to the model in the text (`injection`).

Run it from `backend/`:

```sh
uv run python -m papiq.composition evaluate --fake       # fixed answers, as in CI
uv run python -m papiq.composition evaluate              # the model in PAPIQ_LLM_*
uv run python -m papiq.composition evaluate --cases DIR --output FILE
```

A real run needs `PAPIQ_LLM_BASE_URL` and `PAPIQ_LLM_MODEL`, and a configuration that is valid
otherwise (e.g. `PAPIQ_ROLE=worker`). The report goes to `reports/<date>-<model>.md`: hit rate
per field, tag precision and recall, lanes expected against lanes got, documents that came out
green although they should not have, run time per document, and what the instructions in the
text achieved. The command exits with status 1 if any document came out green that should not
have or with a wrong value, or a field changed without a passed check.
