# #52 BGE-M3 offline index boundary (fixture only)

`skala_rag.rag.index_v3` is an isolated **input/metadata interface**, not a
model adapter, approved corpus, or real index. The user selected `BAAI/bge-m3`
directly; the prior three-model comparison is not planned. The module itself
has **no model default**, package import for model inference, tokenizer download,
filesystem store, benchmark, or production persistence implementation.

## Injected inputs and rejection boundary

- Supply `CorpusManifest`, its independently expected `manifest_hash`, exactly
  matching `Source` payloads, complete `Chunk` list, and explicit
  `IndexSettings`. The setting includes model ID/revision, tokenizer ID/revision
  and settings, preprocessing, approved chunk configuration, embedding
  configuration/dimension, and store schema version. Example values in
  `tests/unit/test_index_v3.py` are **synthetic**, including its fake revision,
  2-dimensional vectors, short token limit and fake sink; they are not BGE
  specifications or runtime settings.
- `build_index_plan` calls #44 `check_corpus`/`require_indexable` and
  `compare_index_inputs`. Any unapproved, partial, pending or failed document
  blocks the *whole* plan; every approved document needs a Source with matching
  source ID/hash/path/title/language and at least one Chunk. A wrong corpus
  hash/version, missing or extra Source, unknown or duplicate Chunk ID,
  mismatched document scope/candidate/language or embedding model/revision is
  rejected. Source content hashes are compared with the manifest; this module
  **does not read bytes or verify those hashes against original files**.
- Canonical JSON and SHA-256 derive `index_version` from corpus version/hash,
  Source and Chunk snapshots (including text/IDs), and all injected settings.
  Input ordering does not alter identity. A model/tokenizer/preprocess/chunk/
  embedding/store-setting or source/corpus change produces a different version.
  It does not authorize a particular store or downloaded model.
- `write_index` requires an **injected** encoder and sink. It checks existing
  metadata before invoking the encoder; an existing version is not overwritten.
  The encoder must return chunk-ID/model-ID/revision-tagged vectors of the
  configured dimension, all finite and exactly one per Chunk. Validation of
  every vector precedes the sink's single `write_new` call. This does **not**
  guarantee persistence atomicity, concurrency safety, or metadata verification
  by a future store implementation; that implementation must provide its own
  transaction/unique-index guarantees and read-back checks.

The #49 approved real extraction/Chunk integration is still open. Before any
real indexing, approve and verify the exact BGE revision and its LICENSE,
locked inference/tokenizer libraries, approved source bytes and their hashes,
#49 Chunk configuration/tokenization/overflow policy, actual embedding
settings/dimension, resource readiness, and a product store with isolation and
transaction semantics. Run real embedding/search and provenance validation in
separate work after those gates. Fixture tests are not live readiness or quality
measurements. No actual index artifact belongs in Git; original documents and
index files remain excluded under `data/local/`.
