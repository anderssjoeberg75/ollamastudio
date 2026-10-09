<!--
name: "Data: Plugin manifest types field"
description: "Schema description for the plugin manifest types field naming a self-contained .d.ts type contract for the noun the plugin's hooks module adds in engine.create, which dependent mods receive under .claude-plugin/types and claude plugin validate checks"
ccVersion: "2.1.287"
-->
Path to the plugin's type contract, relative to the plugin root and starting with ./: a self-contained .d.ts (no import, export-from, require or reference) that exports the types of the noun the plugin's hooks module adds in engine.create at its top level and declares the noun in a `declare module 'claude-code' { interface EngineInterface { ... } }` block, nothing else. A mod that lists this plugin under dependencies gets the contract laid at .claude-plugin/types/<plugin>/index.d.ts each time the engine loads it from a folder the person owns, so it types against the noun with nothing copied; claude plugin validate checks the file.
