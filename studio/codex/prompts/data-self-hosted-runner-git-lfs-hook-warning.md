<!--
name: "Data: Self-hosted runner git-lfs hook warning"
description: "Runner warning that git-lfs's pre-push hook never runs inside lifecycle hooks, so a post-session git push sends LFS pointers without their objects, and how to push the objects or point core.hooksPath instead"
ccVersion: "2.1.280"
variables:
  - "RUNNER_GIT_CONFIG_SCAN"
  - "IS_RUNNER_ROOT"
-->
[runner:warn] git-lfs is set up in the ${RUNNER_GIT_CONFIG_SCAN.lfsFilterScopes.join(" and ")} git config and this runner has a post-session hook. Git run inside the runner's lifecycle hooks runs no repository or global git hooks, so git-lfs's pre-push hook does not run there and a plain "git push" from the post-session hook sends LFS pointers WITHOUT their objects. The runner cannot see whether your hook already handles this: in it, run "git-lfs push <remote> <branch>" BEFORE "git push" (or "git-lfs push --all <remote> <branch>" after it), or point core.hooksPath, ${IS_RUNNER_ROOT?"as":"in the system git config or as"} a GIT_CONFIG_KEY_n/GIT_CONFIG_VALUE_n pair in the runner's environment, at a root-owned directory that holds git-lfs's pre-push hook. The session's own git is unaffected.
