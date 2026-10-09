<!--
name: "Data: Sandbox deniedResolvedAddresses setting"
description: "Schema description for the sandbox deniedResolvedAddresses setting listing IP addresses or CIDR ranges an allowed hostname must not resolve to, its interaction with allowedDomains IP literals, and its exemption for parentProxy and mitmProxy routes"
ccVersion: "2.1.284"
-->
IP addresses / CIDR ranges (IPv4 or IPv6, unbracketed) that an allowed HOSTNAME must not resolve to, in addition to the built-in set (see README "Resolved-address check") and any IP literal listed in deniedDomains. A permitted name that resolves only into these is refused instead of dialed. A name may resolve to a denied address only if that IP literal (and port) is itself in allowedDomains. Not evaluated for connections routed through parentProxy (including one taken from HTTP_PROXY/HTTPS_PROXY) or mitmProxy (that hop resolves the name).
