<!--
name: "Tool Parameter: Artifact database access level"
description: "Describes the read_db and write_db as_level parameter for checking artifact access rules as a view, interact, or admin user, which only narrows access, keeps the caller's identity, and makes refused writes read as not found and refused reads as empty"
ccVersion: "2.1.284"
-->
read_db and write_db only: act at this access level instead of your own, to check what the page's access rules let such a user do — 'view' is someone the artifact is shared with who can only view it, 'interact' any signed-in viewer who can use the page, 'admin' someone who can edit it. It narrows, never raises, your access and keeps your identity (`me` is still you); at 'view' nothing can be written, your own data/users subtree included. At a lowered level a write the rules refuse reads as not found and a refused read as empty. Omit it to act as yourself.
