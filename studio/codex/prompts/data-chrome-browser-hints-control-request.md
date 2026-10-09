<!--
name: "Data: Chrome browser hints control request"
description: "Documents the set_chrome_browser_hints control request that tells a session which connected Claude in Chrome browser to prefer, and that the hints are session-only and never override the live relay roster"
ccVersion: "2.1.274"
-->
@internal The host's view of which Claude in Chrome browser this session should use when several are connected to the account: `preferredDeviceId` is the browser its user picked last on that computer, `localDeviceIds` the relay device ids it knows to be running on that computer right now, `hostPlatform` that computer's OS (for the weaker same-OS label). Session memory only, never persisted; the built-in claude-in-chrome server reads them as its persisted pick and its on-this-computer set, so it defaults to that browser instead of asking. Hints only: nothing is selected unless it is in the live relay roster, and an empty list clears them.
