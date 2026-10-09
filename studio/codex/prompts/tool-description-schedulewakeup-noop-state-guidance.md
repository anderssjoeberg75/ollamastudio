<!--
name: "Tool Description: ScheduleWakeup noop state guidance"
description: "Defines when ScheduleWakeup should mark a tick as noop, how quiet noop streaks collapse in the terminal, and why stopping omits the field"
ccVersion: "2.1.284"
-->
Set `noop: true` if nothing changed — you checked and there's nothing to report ("no change", "still waiting", "quiet hold"). Set `noop: false` if something happened worth keeping — you edited a file, posted a message, advanced state, or surfaced a finding. Consecutive `noop: true` ticks are collapsed in the user's terminal view and tracked as a streak, so long quiet holds stay legible to the user without scrolling. Omit `noop` when stopping (`stop: true`).
