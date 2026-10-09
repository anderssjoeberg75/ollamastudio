<!--
name: "Tool Description: SearchPlugins result handling"
description: "Describes SearchPlugins results and when to render an install card, relay matches, or continue silently"
ccVersion: "2.1.280"
-->
Returns a ranked list with id, name, description, and whether the plugin is already enabled for this session (in a channel session, whether the channel has it). When results fit and SuggestPluginInstall is among your tools, call it to render the install card; otherwise relay the relevant results in text instead. If nothing relevant, proceed without mentioning that you searched.
