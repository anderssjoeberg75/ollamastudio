<!--
name: "Data: Interrupt send_now message_uuid parameter"
description: "Schema description for the interrupt message_uuid parameter read only beside send_now, which restricts the request to one exact-match waiting user message and answers nothing_waiting when that message is not waiting"
ccVersion: "2.1.287"
-->
@internal Read only beside send_now:true: the uuid of the user message that Send now was pressed on, in the very form in which that message reached the CLI (a session service may deliver it in lower case): it is compared as an exact string, as cancel_async_message compares. With it only that message counts as waiting. If the running turn has already taken it in or was started for it, or the CLI does not know it or does not count it as a person's, the request does nothing and answers 'nothing_waiting', whatever else waits. Once the request acts, it acts as without the name: everything queued decides between the move to the background and the abort, and goes along with the named message. An empty string is read as no name, and so is a null, which this schema does not admit but an encoder may write; any other value that is no string names nothing.
