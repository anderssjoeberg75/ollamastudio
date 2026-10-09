<!--
name: "Data: Self-hosted runner Anthropic git proxy credential warning"
description: "Runner warning that sources on hosts not routed through Anthropic-managed git clone with this machine's credentials, which --use-anthropic-git-proxy wipes from HOME-level git config, so credentials must live in /etc/gitconfig, GIT_ASKPASS, or an SSH key"
ccVersion: "2.1.283"
variables:
  - "PLURALIZE_FN"
  - "UNGOVERNED_SOURCE_HOSTS"
-->
[runner:warn] governed git: this session has ${PLURALIZE_FN(UNGOVERNED_SOURCE_HOSTS.length,"a source","sources")} on ${UNGOVERNED_SOURCE_HOSTS.join(", ")}, which the server does not route through Anthropic-managed git; ${PLURALIZE_FN(UNGOVERNED_SOURCE_HOSTS.length,"it is","they are")} cloned with this machine's own git credentials. This runner was started with --use-anthropic-git-proxy, which deletes and replaces the runner account's HOME-level git config at startup and before every session, so a credential for ${PLURALIZE_FN(UNGOVERNED_SOURCE_HOSTS.length,"that host","those hosts")} must live where that flag does not reach (for example /etc/gitconfig, GIT_ASKPASS, or an SSH key with --git-ssh-rewrite). Without one, the clone of a private repository there fails.
