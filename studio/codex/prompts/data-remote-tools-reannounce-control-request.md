<!--
name: "Data: Remote tools reannounce control request"
description: "Documents the remote_tools_reannounce control request a replacement worker sends so the attached client re-announces a machine missing from its tool roster, including worker-epoch, once-per-epoch, ten-second and deadline rules"
ccVersion: "2.1.280"
-->
@internal A replacement worker asks the attached client that serves this session's tools to announce it, when a call names a machine its roster lacks. Not addressed to an instance: the worker knows none. A client that serves the machine announces, overriding once what it believes the worker already holds; it honours one ask per epoch, another only when the announce that one made failed or went unanswered, and at most one per ten seconds, and does nothing for an ask it cannot date or read, or whose sender it would refuse a tool call from. Any other client, and a client that predates the request, does nothing or error-replies, and the worker reads no reply: it withdraws the request with control_cancel_request when its wait ends.
