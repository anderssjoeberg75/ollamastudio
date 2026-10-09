<!--
name: "Data: Sandbox root without CAP_SETFCAP error"
description: "Linux sandbox dependency error explaining that running as uid 0 without CAP_SETFCAP makes every sandboxed command fail while writing a uid map on kernels 5.12+ and backports, and telling the user to grant CAP_SETFCAP or run as non-root"
ccVersion: "2.1.284"
-->
running as uid 0 without CAP_SETFCAP in this process's capability bounding set - on kernels that enforce the CAP_SETFCAP requirement for mapping uid 0 into a user namespace (Linux 5.12 and distribution backports) every sandboxed command fails while writing a uid map ("Operation not permitted"). Grant CAP_SETFCAP to this process, or run as a non-root user
