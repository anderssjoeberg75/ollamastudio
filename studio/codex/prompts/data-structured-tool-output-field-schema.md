<!--
name: "Data: Structured tool output field schema"
description: "Schema description for the user-message tool_use_result field, including per-tool output shapes, the completed Agent or Task output contract, and the detachedToolCall placeholder for calls that stepped aside for a user message"
ccVersion: "2.1.287"
-->
Structured tool output — the tool's full Output object, not the string content sent to the model. The shape is per-tool, keyed by the matching tool_use block's name (see the *Output types in toolTypes); MCP and dynamic tools carry their own shapes, so the field stays unknown-typed. For the Agent/Task tool the completed shape is the subagent's final report without the model-directed agentId/usage trailer, plus run totals — render from it instead of parsing the tool_result text. A call that stepped aside for a message the user sent (today a WebFetch or WebSearch call) carries `{ detachedToolCall: true }` in place of its Output: the call is still running, and its result reaches the model in a later turn.
