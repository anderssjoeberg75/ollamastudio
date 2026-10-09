<!--
name: "Tool Parameter: Artifact database batch writes"
description: "Defines the Artifact writes parameter for batched database mutations, including per-document version pins and atomic or sequential commit behavior"
ccVersion: "2.1.284"
variables:
  - "MAX_BATCH_DATABASE_WRITES"
  - "HAS_ARTIFACT_DB_STR_REPLACE"
-->
write_db with db_op 'batch' only: the writes to apply together, 1-${MAX_BATCH_DATABASE_WRITES} entries of {op: 'set'|'update'|'delete', collection, doc_id, and for set/update exactly one of data (inline object) or file_path (a local JSON file)${HAS_ARTIFACT_DB_STR_REPLACE?", plus if_version — that document's last-read `version`, required for every entry whose document already exists (omit it only when creating); if any pinned document has changed since, or an existing document's entry carries no pin, the whole batch writes nothing and the result names the first such entry":""}}. Each document is addressed at most once and the whole batch body is at most 1 MiB; the batch commits all-or-nothing where the server supports it, else ${HAS_ARTIFACT_DB_STR_REPLACE?"(a batch with no pinned entry) ":""}in order one at a time (the result says which). Prefer it over separate write_db calls whenever you write more than a couple of documents.
