# Search evaluation 2026-10-07 20:11 - `qwen3-embedding:0.6b`

- Embedding endpoint: `10.30.2.15:11434`
- Index: Meilisearch at 127.0.0.1:7701, a temporary index per model
- PAPIQ_SEARCH_CHUNK_SIZE: `1500`
- PAPIQ_SEARCH_MAX_CHUNKS: `8`
- PAPIQ_SEARCH_MAX_TEXT: `200000`
- PAPIQ_SEARCH_SEMANTIC_RATIO: `0.5`
- PAPIQ_EMBEDDING_DOCUMENT_PREFIX: ``
- PAPIQ_EMBEDDING_QUERY_PREFIX: `Instruct: Given a search query, retrieve relevant documents that answer the query
Query:`
- Documents: 28, queries: 48
- Result per query: the first expected case among the first 10 hits. Hit@k: the share of queries found within the first k hits; MRR: mean of 1/rank (0 if not found).

## Results

| Model | Ratio | Hit@1 | Hit@3 | Hit@5 | MRR | Mean ms | p95 ms | Without meaning |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| qwen3-embedding:0.6b | 0 | 67% | 69% | 69% | 0.677 | 4 | 9 | 0 |
| qwen3-embedding:0.6b | 0.5 | 98% | 100% | 100% | 0.990 | 106 | 216 | 0 |
| qwen3-embedding:0.6b | 1 | 83% | 92% | 94% | 0.885 | 87 | 166 | 0 |

Ratio 0 is full text only, 1 is meaning only. 'Without meaning': queries of a ratio above 0 for which the embedding of the query failed, so only the words counted.

## Hit@3 by kind of query

| Model | Ratio | exact (10) | number (5) | compound (6) | typo (2) | paraphrase (17) | english (6) | late (2) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| qwen3-embedding:0.6b | 0 | 100% | 100% | 67% | 100% | 53% | 17% | 100% |
| qwen3-embedding:0.6b | 0.5 | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| qwen3-embedding:0.6b | 1 | 90% | 60% | 100% | 100% | 100% | 100% | 50% |

## Cost of indexing

| Model | Dimensions | Sections | Per document | Embedding s | Rebuild s | s per section |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| qwen3-embedding:0.6b | 1024 | 35 | 1.2 | 48.7 | 49.2 | 1.39 |

## Misses

### qwen3-embedding:0.6b, ratio 0.5

Every query is within the first 3 hits.
