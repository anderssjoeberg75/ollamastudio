## Test-driven development (mandatory)

All production code changes follow TDD — see [`.agents/rules/tdd.md`](.agents/rules/tdd.md):
- Write a failing `unittest` test first, run it and watch it fail for the right reason, then write the minimal code to pass.
- Bug fixes start with a test that reproduces the bug.
- Before touching an untested module, add characterization tests and prove them by mutation.
- Run the full suite before calling anything done: `python3 -m unittest discover -s tests`.
- Coverage gate: `fail_under` in `.coveragerc` may only go up; raise it when your change raises coverage.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
