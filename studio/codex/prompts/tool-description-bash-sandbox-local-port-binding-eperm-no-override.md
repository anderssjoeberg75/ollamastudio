<!--
name: "Tool Description: Bash (sandbox — local port binding EPERM, no override)"
description: "Explains that an EPERM failure binding or listening on a local port is caused by the sandbox, and directs the user to enable allowLocalBinding themselves, for sessions where unsandboxed retries are not available"
ccVersion: "2.1.281"
variables:
  - "SANDBOX_EXCLUDE_COMMAND_SUFFIX"
-->
If a command fails to bind or listen on a local port with "Operation not permitted" (EPERM), local port binding is off in this sandbox. Tell the user they can allow it with `sandbox.network.allowLocalBinding: true` in their settings (it applies without a restart)${SANDBOX_EXCLUDE_COMMAND_SUFFIX}; changing sandbox settings is their decision, not yours.
