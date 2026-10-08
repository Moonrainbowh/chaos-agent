# Windows root integration suite timeout diagnosis

Status: diagnosis complete; no product, deadline, CI, or test assertions changed by this investigation.

The two old Windows jobs exhausted the **aggregate 600-second root suite deadline**. The evidence does not support an SSL deadlock. Python 3.13 was in `ssl.create_default_context` when faulthandler sampled it, then continued into peer-runtime tests within 0.782 seconds and more tests before the supervisor terminated the process. Python 3.10 instead sampled an active SQLite commit. Both suites were still progressing through different tests, rather than stuck on a common provider operation.

## Physical evidence

- `ci-windows313-first-job.log`: root suite starts at `2026-10-06T15:12:35.9629962Z`; faulthandler at `15:22:36.0565379Z`; peer runtime activity at `15:22:36.8385223Z`; supervisor exit 124 at `15:22:37.9776644Z`. The timeout stack is the local construction of a provider HTTP client in `MultimodalRuntimeTests.test_real_factory_binds_shared_store_and_profile_modalities`, with no HTTP request in that test.
- The same log records all eight HostProgressIntegration setup calls between `15:19:05.3016352Z` and `15:22:13.8008673Z` (188.499 seconds between first and last setup). This is genuine integration work with repeated actual SQLite transactions, including multiple subtest tasks.
- `ci-windows310-first-job.log`: timeout stack includes `_database_cancellation.py:103 commit`, and active root test is `test_parent_or_whole_scope_does_not_renew_for_excluded_directory`. This is not an SSL stack.
- `windows-host-progress-profile.log`: isolated real `test_long_related_read_only_trace_without_prose_passes_five_turns` PASS, 1 test in **16.639 seconds**. cProfile recorded **1,176 database calls**, **12,201 SQLite execute calls (6.210 seconds)**, **1,183 SQLite close calls (3.224 seconds)** and **38 Git repository checks (1.271 seconds)**. No SSL context construction occurs in this trace. Async function cumulative profile totals overlap; they are not additive wall-clock durations.
- `windows-multimodal-profile.log`: isolated sampled multimodal test PASS, **0.079 seconds**, one real SSL context build **0.009 seconds**, certificate load **0.006 seconds**. This local measurement cannot establish CI certificate loading speed, but disproves a deterministic hang in the sampled test.

Both isolated runs use the existing CPython 3.13.2 candidate venv with explicit main source `PYTHONPATH`. They use owned temporary fixture storage, no real user DB, and do not send a provider request. An initial profile invocation lacked PYTHONPATH and failed import before test execution; it was corrected before the recorded profiling runs. The investigation did not modify source code.

## Meaning and minimum repair path

There is a real performance cost in per-operation SQLite connections and repeated durable task bookkeeping, particularly on Windows. It must not be hidden by disabling FULL synchronization, mocking the database, dropping real task rounds, or increasing waits. The user-authorized new prompt defaults (20,000 tool and 300,000 total) are not evidence that an offline scripted trace needs more wall-clock time: that isolated trace still completes all its fixed rounds and checks.

The smallest test-infrastructure repair is to execute the large root integration collection in deterministic **module groups** under the existing subprocess supervisor, retaining the **600-second deadline for every group** and all current assertions. Preserve module/class fixtures in a single group, refuse missing or duplicate test IDs, prove the group discovery union equals full discovery, and sum all discovered/run/skipped/failure/error counts into the final result. No individual test or module should receive a longer watchdog than before. Include an intentionally hung child, child-tree cleanup, a failing group followed by later groups, and discovery mismatch cases in the runner verification. This removes an accidental single aggregate deadline shared by hundreds of unrelated integration tests without changing product behavior or deleting test coverage.

This is a proposed repair, not an implemented or CI-validated PASS. Retain the original full-root timeout logs, report aggregate duration honestly, and require the new Windows runs to complete all expected IDs. A product-level persistent SQLite connection or batching optimization would require separate lifecycle/cancellation/migration design and is broader than a CI repair; this diagnosis does not authorize one.

## Concrete inventory and scoped runner plan

`count_root_modules.py` only discovers the current main root tests. Its physical `windows-root-module-inventory.json` contains **701 tests, 701 distinct IDs, 132 modules**. The largest modules are workspace policy (30), rewind gate (14), ACP task service / command integration / rewind capture / rewind runtime as-of / rewind runtime integrity (13 each); the costly HostProgressIntegration module has only 8 tests, showing that test count alone is a poor elapsed-time grouping proxy. The inventory contains every ID, with no `_FailedTest` import placeholder observed.

The simplest deterministic grouping is **one existing `test_*.py` module per group**, sorted by repository-relative path. This preserves all module and class fixtures, needs no heuristics or time-based grouping, and bounds every genuine individual module by the unchanged 600-second supervisor. An unusually slow/hung module remains a hard failure. The root collection remains **one logical suite** in the 30-suite outer summary; its 132 supervised group results live in a separate `groups` field. Group process startup overhead is a real tradeoff and must be included in recorded aggregate time.

Implement only after review, with an explicit Windows CI flag such as `--split-root-modules`. Default runner and non-Windows CI continue unchanged. Do not silently turn the 30-suite acceptance into 161 apparent suites.

Required file scope:

1. `scripts/run_tests.py`: opt-in flag; only root suite takes the grouped path. Aggregate discovered/run/skipped/failures/errors into one root entry. A ordinary failure or timeout continues to the next group and contributes nonzero logical root status. An infrastructure/cleanup failure stops immediately, preserves failed group, and names all unrun groups and later logical suites; totals must not imply complete execution.
2. `scripts/suite_process.py`: optional exact module pattern passed to the existing child `--pattern` argument; Windows Job creation, gate-before-discovery, existing deadline and cleanup behavior retained. A supervised full discovery-only preflight produces the expected ID manifest before grouping; it also uses a finite 600-second bound.
3. `scripts/run_test_suite.py`: discovery-only result mode plus explicit discovered/executed ID lists in a separate metadata record. Preserve existing numeric `counts` schema so the parent does not accidentally sum lists. Module group discovery IDs must equal the preflight IDs belonging to that module; the final union must equal full discovery. Prefer module filename/path ownership from discovery, rather than parsing class names by a fixed number of dot segments. Refuse a group pattern with no matches.
4. `tests/test_run_tests_script.py`, `tests/test_run_test_suite_script.py`, `tests/test_suite_timeout.py`: narrow regression cases for opt-in-only grouping, fixture preservation, exact union, duplicate/missing/module-import errors, ordinary failing-group continuation and aggregate exit, hung child cleanup, cleanup failure immediate stop/unrun accounting. Existing `test_suite_timeout` real Windows child-tree oracle must remain enforced.
5. Root integration contract/extension: describe explicit grouped Windows invocation and unchanged logical counts/600-second cleanup contract. `.github/workflows/ci.yml`: add only the opt-in flag to the Windows test invocation; portable invocations remain unchanged.

The existing 701-test manifest is the pre-change baseline. Any legitimate new runner tests increase it; record that delta explicitly and derive final expected IDs afresh instead of forcing the final count back to 701. Existing candidate failed logs remain historical evidence and are not overwritten by grouped results.

## Reproduce the isolated probes

From the main checkout in PowerShell, set process-only `PYTHONPATH` to `F:/code-ai-chaos/chaos-16-agent/src;F:/code-ai-chaos/chaos-16-agent`, then run the candidate venv interpreter:

```powershell
python -m cProfile -s cumulative -m unittest tests.test_host_progress_integration.HostProgressIntegrationTests.test_long_related_read_only_trace_without_prose_passes_five_turns
python -m cProfile -s cumulative -m unittest tests.test_multimodal_runtime.MultimodalRuntimeTests.test_real_factory_binds_shared_store_and_profile_modalities
```

The probe measures one Windows host and does not replace complete cross-platform suite acceptance.
