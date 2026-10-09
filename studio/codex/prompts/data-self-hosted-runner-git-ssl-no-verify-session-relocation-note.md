<!--
name: "Data: Self-hosted runner GIT_SSL_NO_VERIFY session relocation note"
description: "Runner warning addendum explaining that inside a session GIT_SSL_NO_VERIFY becomes http.sslVerify=false that no longer covers the git mount or governed hosts, that per-server sslVerify takes precedence, and to name a company CA on certificate failures"
ccVersion: "2.1.283"
variables:
  - "PLURALIZE_FN"
  - "GOVERNED_HOSTS"
-->
 Inside a session the runner takes it out of the environment and gives git http.sslVerify=false instead, which still applies to your own servers (with one exception the runner guide describes: the git that makes a new working copy), and no longer to Anthropic-managed git (the git mount) or to the ${PLURALIZE_FN(GOVERNED_HOSTS.length,"host")} the server governs (${GOVERNED_HOSTS.join(", ")}): their certificates are checked, because git inside a session carries the session's token to the mount and reaches the governed ${PLURALIZE_FN(GOVERNED_HOSTS.length,"host")} through the session's relay. An http.<url>.sslVerify of your own for a server, in any git config file, now takes precedence over it for that server. If git inside a session then fails a certificate check at the mount or a governed host (behind a proxy that re-signs traffic, say), name your company's certificate authority in GIT_SSL_CAINFO.
