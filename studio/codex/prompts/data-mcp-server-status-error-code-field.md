<!--
name: "Data: MCP server status error_code field"
description: "Schema description for the MCP server status error_code field, naming the host-fixable failure causes — a rejected claude.ai connector login, a rejected first-party Anthropic credential, and a project-scoped server awaiting approval — and noting that it is absent for every other failure and that new values are additive"
ccVersion: "2.1.275"
-->
@internal Why a 'failed' row failed, for the causes a host can fix: CLAUDEAI_BEARER_REJECTED (a claude.ai connector rejected the claude.ai login — sign in again), FIRST_PARTY_AUTH_REJECTED (a first-party Anthropic server rejected its credential — the claude.ai login, or for the Claude Design server its /design-login authorization; the row's error names which sign-in fixes it), APPROVAL_REQUIRED (a project-scoped server awaiting the user's approval). Absent for every other failure; new values are additive.
