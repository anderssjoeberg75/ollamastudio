<!--
name: "Data: Network-path settings file unread recovery guidance"
description: "Guidance appended to plugin uninstall results when a settings file could not be read because its path may cross a link to another machine, telling the person to replace the link or use the real location before retrying"
ccVersion: "2.1.292"
variables:
  - "LINK_SUSPECT_UNREAD_SETTINGS"
  - "SETTINGS_SOURCE_KEY"
  - "SETTINGS_SOURCE_BINDING"
  - "RETRY_INSTRUCTION"
  - "NETWORK_PATH_UNREAD_SETTINGS"
-->
If a file that was not read, its ".claude" folder or a folder above them is a link to another machine, replace the link with a real file or folder, or ${LINK_SUSPECT_UNREAD_SETTINGS.every(({SETTINGS_SOURCE_KEY:SETTINGS_SOURCE_BINDING})=>SETTINGS_SOURCE_BINDING==="projectSettings"||SETTINGS_SOURCE_BINDING==="localSettings")?"start Claude Code from the folder's real location":"use the real location instead of the link when you start Claude Code or add a directory (for example with --add-dir)"}. Then ${RETRY_INSTRUCTION}${LINK_SUSPECT_UNREAD_SETTINGS.length<NETWORK_PATH_UNREAD_SETTINGS.length?". If you see this again with no such link, a path cannot be checked, and trying again will not help":""}
