# Search evaluation 2026-10-07 20:10 - `snowflake-arctic-embed2`

- Embedding endpoint: `10.30.2.15:11434`
- Index: Meilisearch at 127.0.0.1:7701, a temporary index per model
- PAPIQ_SEARCH_CHUNK_SIZE: `1500`
- PAPIQ_SEARCH_MAX_CHUNKS: `8`
- PAPIQ_SEARCH_MAX_TEXT: `200000`
- PAPIQ_SEARCH_SEMANTIC_RATIO: `0.5`
- PAPIQ_EMBEDDING_DOCUMENT_PREFIX: ``
- PAPIQ_EMBEDDING_QUERY_PREFIX: `query:`
- Documents: 28, queries: 48
- Result per query: the first expected case among the first 10 hits. Hit@k: the share of queries found within the first k hits; MRR: mean of 1/rank (0 if not found).

## Results

| Model | Ratio | Hit@1 | Hit@3 | Hit@5 | MRR | Mean ms | p95 ms | Without meaning |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| snowflake-arctic-embed2 | 0 | 67% | 69% | 69% | 0.677 | 3 | 8 | 0 |
| snowflake-arctic-embed2 | 0.5 | 100% | 100% | 100% | 1.000 | 67 | 139 | 0 |
| snowflake-arctic-embed2 | 1 | 94% | 98% | 98% | 0.955 | 68 | 135 | 0 |

Ratio 0 is full text only, 1 is meaning only. 'Without meaning': queries of a ratio above 0 for which the embedding of the query failed, so only the words counted.

## Hit@3 by kind of query

| Model | Ratio | exact (10) | number (5) | compound (6) | typo (2) | paraphrase (17) | english (6) | late (2) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| snowflake-arctic-embed2 | 0 | 100% | 100% | 67% | 100% | 53% | 17% | 100% |
| snowflake-arctic-embed2 | 0.5 | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| snowflake-arctic-embed2 | 1 | 100% | 100% | 100% | 100% | 100% | 100% | 50% |

## Cost of indexing

| Model | Dimensions | Sections | Per document | Embedding s | Rebuild s | s per section |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| snowflake-arctic-embed2 | 1024 | 35 | 1.2 | 20.4 | 20.9 | 0.58 |

## Misses

### snowflake-arctic-embed2, ratio 0.5

Every query is within the first 3 hits.
