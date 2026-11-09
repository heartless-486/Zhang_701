# AI deployment and maintenance contract

1. Read README.md and docs/DEPLOY_AI.md. This is an ADS execution package, not the original MMIC/RL application.
2. Use the configured licensed local ADS installation. Never download or install ADS/PDKs or modify license configuration.
3. Use a dedicated Python 3.11–3.13 venv. Keep all absolute machine paths in the untracked ads-mcp.toml. Do not place credentials in configuration, logs, examples or commits.
4. Run doctor, offline tests and scripts/check_mcp.py before native work. capabilities reads the installed official MCP schemas without asking start_local_session.
5. Native smoke work must create a new disposable workspace. Do not close, connect to, save or mutate a user's GUI session implicitly.
6. Simulations and EM are real paid attempts. Preserve job IDs, input hashes, raw outputs and failures; never silently retry, resume, overwrite evidence, or expand a finite budget. No RL training or optimization is part of deployment.
7. Keep ADS/OA and PDK locks intact. Use separate workspaces for parallel candidates; max_workers defaults to one. Measure resources, license availability and throughput before raising it. Preserve external solver thread settings.
8. For Momentum, accept only already-preprocessed complete source inputs with explicit hashes. Verify native port width/planes/materials and S/noise validity separately before scientific use.
9. Do not call file-presence checks or protocol tests RF qualification. See docs/VALIDATION.md for evidence scope.
10. Prefer structured tools and version-matched official docs. Arbitrary Python is opt-in trusted execution and is not a security sandbox.
11. Retain upstream license provenance. No code may be copied from unlicensed, noncommercial or GPL projects into this MIT package without a deliberate compatible licensing decision.
12. A publication request authorizes a normal non-force push to the named repository after checks. Preserve any existing remote content and never force push or overwrite unrelated history.
