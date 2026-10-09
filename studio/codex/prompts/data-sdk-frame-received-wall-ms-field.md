<!--
name: "Data: SDK frame_received_wall_ms field"
description: "Schema description for the SDK turn-timing frame_received_wall_ms field recording when the triggering send's frame arrived on a session reading input from the session server's SSE stream, how it pairs with the enqueue and turn-start timestamps to split transit from queue wait, and when it is absent"
ccVersion: "2.1.277"
-->
@internal Date.now() (epoch ms) when the triggering send's frame arrived on a session reading its input from the session server's SSE stream (--sdk-url), before the input loop read it. With frame_enqueued_wall_ms and turn_started_wall_ms it splits the stretch between the server's persist and request_sent_wall_ms into transit, the input loop's handling of the frame, its wait on the command queue, and the turn's own work. Present only together with request_sent_wall_ms, and only when the transport recorded the receipt; absent on a local stdin host and from older producers.
