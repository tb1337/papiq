# Evaluation set

Synthetic German documents (no real personal data) with the metadata expected of them. They run
through the same classification and field extraction steps as the pipeline, to compare
language models and to check that the checks catch wrong answers.

- `master_data.json`: contacts, document types, tags and fields the documents are matched
  against; optionally `contact_aliases` (other names by contact) and `type_descriptions`
  (by document type).
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

## Search

`search/queries.json` holds queries for the same documents (`query`, `kind`, `expected`: the
cases that answer it). The kinds are `exact` (words of the document), `number` (identifiers and
amounts), `compound` (compound words), `typo`, `paraphrase` (other words for the same thing),
`english` (query in English, documents in German) and `late` (only in the back part of the long
statement, beyond the sections that get a vector).

```sh
uv run python -m papiq.composition evaluate-search --fake                # flow only, as in CI
uv run python -m papiq.composition evaluate-search --models bge-m3,other # models of the endpoint
uv run python -m papiq.composition evaluate-search --ratios 0,0.5,1 --queries FILE --output FILE
```

Each model gets a temporary Meilisearch index (`papiq-eval-…`, removed afterwards) and the
documents of the set, indexed by the real indexing service; the queries run through the real
search service at each semantic ratio. A real run needs `PAPIQ_MEILISEARCH_URL`,
`PAPIQ_EMBEDDING_BASE_URL` and `PAPIQ_EMBEDDING_MODEL` (the default for `--models`), and
`PAPIQ_EMBEDDING_DIMENSIONS` with any value, because the configuration is checked first (the
real length is measured). Prefixes, section size and the rest come from the `PAPIQ_` settings, so
a model that needs other prefixes gets a run of its own. `--fake` uses bag-of-words vectors and
the in-memory index: it shows that the flow works, not how good a model is.

The documents carry a number as title and file name, so a query cannot find a case by its name.
The report goes to `search/reports/<date>-<models>.md`: Hit@1/3/5 (the share of queries whose
answer is among the first 1, 3 or 5 hits), mean reciprocal rank, time per query, Hit@3 per kind
of query, the cost of indexing (sections, seconds per section) and the queries that missed.
