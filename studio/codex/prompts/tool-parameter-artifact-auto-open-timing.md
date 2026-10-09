<!--
name: "Tool Parameter: Artifact auto-open timing"
description: "Describes the auto_open parameter on a type_url Artifact create: when to pass after_first_write so the user does not first see an empty Artifact, and when to omit it so the Artifact opens on creation"
ccVersion: "2.1.277"
variables:
  - "HAS_ARTIFACT_DB"
-->
Only with `type_url` and no `file_path`: when the new Artifact opens for the user. Pass "after_first_write" when you will fill it right after creating it (${HAS_ARTIFACT_DB?'a later "write_db", or a files publish to its url':"a later files publish to its url"}), so the user does not first see it empty — it then opens on that first write. Omit it otherwise, and always for a type whose content you write through a connector, such as a Claude Docs document (no publish or store write follows to open it): the Artifact opens when created.
