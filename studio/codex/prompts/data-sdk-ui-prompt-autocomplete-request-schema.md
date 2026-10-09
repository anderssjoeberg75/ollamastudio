<!--
name: "Data: SDK ui_prompt_autocomplete request schema"
description: "Schema description for the ui_prompt_autocomplete request in which a remote surface's Composer asks the engine to raise prompt.autocomplete for the token at the caret, including per-client ordering and superseded handling"
ccVersion: "2.1.292"
-->
@internal A remote surface's own Composer asks what the plugins add to its autocomplete list: the engine raises `prompt.autocomplete` for the token that ends at the caret (the run of characters that are not whitespace), as the terminal's composer does while the person types, and answers the rows for the surface to draw beneath its own. With no token at the caret nothing is raised and no row is answered. One dispatch per client at a time; of the asks that arrive meanwhile only the newest is raised next, and every ask a newer one overtook answers `superseded` with no row. A chain that fails answers no row.
