<!--
name: "Data: Structured usage rate-limit rows field"
description: "Schema description for structured usage rate-limit rows, including server-defined meter ordering, null and empty semantics, and the synthesized header-fallback row"
ccVersion: "2.1.277"
-->
The server's usage rows (the usage endpoint's limits[]), as sent: which meters apply, their scope, labels, severity and order are the server's, so a client renders them verbatim and a new meter needs no client release. Empty when the server reported no meters; null when the body carried no rows at all (a server that predates them). Null too while the usage fetch is failing: the rows here are only ever the server's current reply, so neither the row the CLI builds from rate-limit response headers for its own screen nor its snapshot of an earlier reply appears here.
