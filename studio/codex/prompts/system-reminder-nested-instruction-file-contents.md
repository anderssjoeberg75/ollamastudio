<!--
name: "System Reminder: Nested instruction file contents"
description: "Frames a nested CLAUDE.md or AGENTS.md instruction file's contents by path when the instruction-file module attaches one found below the session's working directory"
ccVersion: "2.1.275"
variables:
  - "INSTRUCTION_FILE"
-->
Contents of ${INSTRUCTION_FILE.path}:

${INSTRUCTION_FILE.content}
