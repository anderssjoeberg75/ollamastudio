<!--
name: "Tool Description: Bash (sandbox — local port binding EPERM, override available)"
description: "Explains that an EPERM failure binding or listening on a local port is caused by the sandbox, and that allowLocalBinding can be enabled without leaving the sandbox, for sessions where unsandboxed retries are already available"
ccVersion: "2.1.281"
-->
If a command fails to bind or listen on a local port with "Operation not permitted" (EPERM), local port binding is off in this sandbox. Treat it as the sandbox-caused failure described above, and tell the user that `sandbox.network.allowLocalBinding: true` in their settings (it applies without a restart) allows it without leaving the sandbox.
