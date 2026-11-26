# Gate 4.5 corrected-fixture evaluation artifacts

The v1 application code was checked out at `9d2a55d24951f5ed21550d506b22971d3e4179de` in a detached worktree. Only the three evaluator fixture files in `fixtures/` were overlaid, byte-identically, onto that checkout to apply the user-authorized offline weather criterion and explicitly convey the workspace. The hardened detached checkout was `5aa4427cbbe51913e13aba4d700dbd65fb547b04`, whose fixture files are identical. The fixture changes are evaluator-only, not a v1 application fix. `diagnostics.json` contains actual observations, while `evaluations/` is the actual independently verified EvalStore and `evidence/` contains the runtime-verified evidence chain. L2 repair probe outputs are simulated, and detector recognition does not prove model refusal.

Fixture SHA-256:

```text
e425c925aaf6c11c987e82a9d24adf9bd44048a15db532b0201d1b5e14c97b25  _eval_routing.py
11f8d325ed47b12caf5b92255df650f41aee439752233d9d5d35c354425c254e  _eval_security.py
d738d5d8a847f168692a62ccc4c2c8072f2eef69017f2eee1f66c269c4074708  benchmark_tasks.json
```

`comparison.json` is the actual `compare_eval_runs` result from reopening and verifying those persisted stores, with default seed, resamples and alpha. The original metric is preserved in `../gate45-baseline/`. Detached pytest logs, coverage, final checks, and test-first router failure logs are also preserved here. All worktrees were removed after verification; the origin probe covers only the explicitly sampled points. Network-family syscall filtering and Git worktrees are not general OS sandboxes.
