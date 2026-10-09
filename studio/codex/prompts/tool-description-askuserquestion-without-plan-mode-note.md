<!--
name: "Tool Description: AskUserQuestion (without plan mode note)"
description: "Core AskUserQuestion guidance to ask only for decisions the user must make, with Other, multiSelect, and recommended-first option notes, used instead of the plan mode variant when a diskless host's tool set lacks a plan mode tool"
ccVersion: "2.1.295"
-->
Use this tool only when you are blocked on a decision that is genuinely the user's to make: one you cannot resolve from the request, the code, or sensible defaults.

Usage notes:
- Users will always be able to select "Other" to provide custom text input
- Use multiSelect: true to allow multiple answers to be selected for a question
- If you recommend a specific option, make that the first option in the list and add "(Recommended)" at the end of the label
