<!--
name: "Data: Self-hosted runner client certificate relay warning"
description: "Runner warning that TLS client certificate variables set on the runner would be offered to the session relay rather than governed hosts, with remedies of asking for direct listing, unsetting the variables, or scoping sslCert and sslKey per URL"
ccVersion: "2.1.283"
variables:
  - "CLIENT_CERTIFICATE_VARIABLES_SET"
  - "PLURALIZE_FN"
  - "GOVERNED_HOST_COUNT"
  - "GOVERNED_HOSTS"
-->
[runner:warn] governed git: ${CLIENT_CERTIFICATE_VARIABLES_SET.join(" and ")} ${PLURALIZE_FN(CLIENT_CERTIFICATE_VARIABLES_SET.length,"is","are")} set on this runner, a TLS client certificate that git offers to every server, and the server governs ${PLURALIZE_FN(GOVERNED_HOST_COUNT,"host")} ${GOVERNED_HOSTS.join(", ")}. Inside a session git reaches ${PLURALIZE_FN(GOVERNED_HOST_COUNT,"that host","those hosts")} through the session's relay, so it is the relay that git would offer the certificate to, and not your company's server: a server that demands a client certificate will refuse the session's git. The runner leaves ${PLURALIZE_FN(CLIENT_CERTIFICATE_VARIABLES_SET.length,"the variable as it is","both variables as they are")}. If ${PLURALIZE_FN(GOVERNED_HOST_COUNT,"that host demands","those hosts demand")} one, ask Anthropic to list ${PLURALIZE_FN(GOVERNED_HOST_COUNT,"it","them")} as direct; otherwise unset the ${PLURALIZE_FN(CLIENT_CERTIFICATE_VARIABLES_SET.length,"variable","variables")} for the runner, or scope the certificate to the servers that need it (http.<url>.sslCert and http.<url>.sslKey in the machine's system git config).
