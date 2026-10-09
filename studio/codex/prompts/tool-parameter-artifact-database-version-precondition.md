<!--
name: "Tool Parameter: Artifact database version precondition"
description: "Defines the Artifact database if_version precondition, required on every write to an existing document and omitted only when creating one, with the write refused until the document has been read and batch entries pinned individually"
ccVersion: "2.1.284"
-->
write_db with db_op 'set', 'update', 'str_replace' or 'delete' (a 'batch' pins each entry in `writes` instead): the document's `version` as you last read it (every read_db document carries it, and so does every set, update and str_replace result). Required on every write to a document that already exists; omit it only when creating one. The write applies only if the document is still at that version: if it changed, nothing is written and the result names the current version, so pin the write instead of re-reading first to check. A write to an existing document that carries no if_version is refused until you read the document.
