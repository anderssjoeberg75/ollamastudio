<!--
name: "Tool Description: Bash (sandbox — explain restriction)"
description: "Explain which sandbox restriction caused the failure"
ccVersion: "2.1.282"
variables:
  - "IS_DESKTOP_DRIVEN_EXTERNAL_HOST_SESSION_FN"
-->
Briefly explain what sandbox restriction likely caused the failure. Be sure to mention that the user can use the `/sandbox` command to ${IS_DESKTOP_DRIVEN_EXTERNAL_HOST_SESSION_FN()?"change the sandbox settings":"manage restrictions"}.
