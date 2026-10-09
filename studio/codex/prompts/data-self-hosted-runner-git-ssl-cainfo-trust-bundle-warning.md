<!--
name: "Data: Self-hosted runner GIT_SSL_CAINFO trust bundle warning"
description: "Runner warning that GIT_SSL_CAINFO prevented building the combined certificate file for Anthropic-managed git, giving the failure cause and first remedy and directing the company CA into a per-server http sslCAInfo entry"
ccVersion: "2.1.283"
variables:
  - "TRUST_BUNDLE_FAILURE_REASON"
  - "TRUST_BUNDLE_FIRST_REMEDY"
  - "UNSET_ALTERNATIVE_LEAD"
-->
[runner:warn] governed git: GIT_SSL_CAINFO is set on this runner and ${TRUST_BUNDLE_FAILURE_REASON}, so the runner did not build the certificate file it gives its own git for Anthropic-managed git (the git mount): the public certificate authorities plus yours. The runner's own fetches from the mount keep the variable as it is, and fail if the file holds only your company's certificate authority, because the mount presents a public certificate. ${TRUST_BUNDLE_FIRST_REMEDY} ${UNSET_ALTERNATIVE_LEAD} the variable for the runner and name your company's file for your own server in the machine's system git config (an http.<your server's URL>.sslCAInfo entry).
