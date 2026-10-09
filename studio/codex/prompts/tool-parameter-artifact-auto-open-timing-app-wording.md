<!--
name: "Tool Parameter: Artifact auto-open timing (app wording)"
description: "Describes the auto_open parameter on a type_url Artifact create in third-person app wording: when Claude passes after_first_write so the person does not first see an empty Artifact, and when it omits the parameter so the Artifact opens on creation"
ccVersion: "2.1.277"
-->
Only with `type_url` and no `file_path`: when the new Artifact opens for the person. Claude passes "after_first_write" when it will fill the Artifact right after creating it with a files publish to its url, so the person does not first see it empty. The Artifact then opens on that first write. Otherwise Claude omits it, and the Artifact opens when created; Claude always omits it for a type whose content it writes through a connector, such as a Claude Docs document, since no publish or store write follows to open it.
