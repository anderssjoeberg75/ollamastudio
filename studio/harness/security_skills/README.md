# Vendored SecuritySkills

Source: https://github.com/UnitOneAI/SecuritySkills (commit `70bc259bb01abb3015ad2ad859ad5253cbf0bcab`), MIT — see `LICENSE`.

Only `skills/`, `index.yaml`, `LICENSE` and `schemas/finding.schema.json` are vendored. The files are read
unmodified by `studio/harness/skills.py`, which:

- picks 2–4 skills per coverage unit (file type, attack class, content signals such as routes or LLM calls),
- condenses them to a profile budget (quick 2.5k / standard 5k / deep 9k characters) for the VDH prompt,
- adds the `scanner-tuning` false-positive playbook to the VVS validator,
- exports findings as schema-valid envelopes in `.vulnerability-harness/securityskills/<skill>.json`.

To update: copy a newer upstream checkout over these paths and bump `UPSTREAM_COMMIT` in `skills.py`.
