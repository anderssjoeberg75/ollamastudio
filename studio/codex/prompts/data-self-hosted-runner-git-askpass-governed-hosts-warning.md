<!--
name: "Data: Self-hosted runner GIT_ASKPASS governed hosts warning"
description: "Runner warning that a GIT_ASKPASS program answers for every host, so later git config could send this machine's credential for governed hosts through Anthropic's relay, and directs scoping or unsetting it along with core.askPass and SSH_ASKPASS"
ccVersion: "2.1.283"
variables:
  - "PLURALIZE_FN"
  - "GOVERNED_HOSTS"
-->
[runner:warn] governed git: GIT_ASKPASS is set on this runner and the server governs ${PLURALIZE_FN(GOVERNED_HOSTS.length,"host")} ${GOVERNED_HOSTS.join(", ")}. An askpass program answers for every host. The session's own git config tells git to give up rather than ask for a credential for ${PLURALIZE_FN(GOVERNED_HOSTS.length,"that host","those hosts")}, because the session relay supplies it. Do not leave it to that alone: git config read after the session's own (a repository's, the command line, GIT_CONFIG_* variables) can switch that off, and if the program were then asked, this machine's credential would pass through Anthropic's relay. Make the program answer only for the hosts you mean (git passes it the URL in its prompt) or unset it; the same goes for core.askPass in git config and for SSH_ASKPASS.
