# S16 actual isolated VSCode ACP acceptance

Both real editor tasks passed against frozen candidate `2810fcbd20661022ffc92147141a4f8aadea043c`. This is acceptance of the owned custom VSCode extension-host harness, not a Python-only ACP unit test or a claim about the user's existing editor plugins or unsaved buffers.

## Actual path

Installed VSCode 1.140.0 (`D:/soft/Microsoft VS Code/bin/code.cmd`) launches an isolated desktop extension test host using owned `--user-data-dir`, `--extensions-dir`, `--extensionDevelopmentPath` and `--extensionTestsPath`. The JavaScript runner requires the actual `vscode` API, creates and shows an OutputChannel plus a WebviewPanel, and starts the Python client bridge from the extension host.

The bridge uses installed official `agent-client-protocol==0.12.1` / `acp.spawn_agent_process` to launch the candidate's actual `python -m chaos_agent.cli --profile glm-5-3-flash --mode medium acp`. It sends real initialize, new_session and prompt requests. Each incoming `session/update` is streamed into the real OutputChannel and webview. The offline collector independently compares the received update count with VSCode's own `window1/exthost/output_logging_*/1-S16 actual ACP acceptance.log`, rather than relying only on the bridge's snapshot file. No visual screenshot inspection is claimed.

The candidate stayed frozen throughout both tasks; Root confirmed the change from prior `4ed1a6a9` to `2810fcbd` affected three root tests only, with product/configuration/protocol files unchanged.

## Actual results

| Task | Real ACP updates in native editor log | Model requests | Worker elapsed | Durable result | Independent fixture check |
| --- | --- | --- | --- | --- | --- |
| analysis | 442 | 2 | 25.064s | completed / unchanged / unverified | All original file hashes unchanged; fixed five tests retain the expected failing baseline |
| modify | 127 | 4 | 26.473s | completed / changed / verified | Only names.py changed; fixed five tests passed; one current `system_verifier` PASS with verifier identity and subject hash |

Both ACP responses were `end_turn`; both agent processes exited 0 and both extension hosts finished. The analysis task deliberately ran no tests; its independent post-task baseline test execution is offline acceptance evidence, not product SYSTEM evidence. The modify task's SYSTEM receipt is independently read from the isolated durable `verification_evidence` table and agrees with `chaos_agent.cli task result`, rather than treating model text or an ordinary shell command as verification.

The protected `AGENTS.md`, `test_names.py` and `pyproject.toml` content matched the fixed v2 fixture. The generated implementation trims strings, omits blanks and preserves duplicate/order/input behavior. The fixture's `pyproject.toml` is present so project discovery exposes the Python unittest verification recipe.

Model/profile/effort are `glm-5.3-flash` / `glm-5-3-flash` / `medium` in both frozen durable task contracts. Both isolated configurations retain 8 model rounds, 24 tool calls, 4096 maximum output tokens, zero provider retries and the 300s ACP prompt deadline. Neither task was retried. The product's unchanged persisted `max_active_seconds=1200` default is distinct from this client's 300s prompt cap.

## Evidence and collection corrections

- Analysis owned artifacts: `C:/Users/Windows11/.codex/artifacts/next-version-s16-vscode-ecd5ce7877a5`; task `d171017703b84e3db2c47a091074ad66`.
- Modify owned artifacts: `C:/Users/Windows11/.codex/artifacts/next-version-s16-vscode-a279ce72aa02`; task `641adb7e1e09449c9ccf841ba1bf7602`.
- Summaries: `vscode-analysis-summary.json`, `vscode-modify-summary.json`. They include native editor log paths, durable result/contract/evidence, hashes, model usage and actual editor-host metadata. Each owned root also preserves `editor-result.json`, `editor-output-channel.txt`, `editor-webview.html`, `persisted-events.json`, independent unittest output and VSCode logs.
- Exact credential scan: `vscode-artifact-audit.json` scanned 140 analysis and 142 modify files, with zero exact-secret matches and zero unreadable files. The credential was only passed in process memory/environment, and provider configuration persists an environment-variable reference.

The initial analysis CLI invocation returned before its extension host completed. It was subsequently collected from the same task after the actual editor result and host exit; no second model call was sent. The launcher now uses `--wait`.

The initial modify launcher completed its actual editor run but its collector used the Conda Python default GBK to read UTF-8 JSON. Collection was repaired offline with explicit UTF-8, without rerunning the model. That early launcher had not persisted its modify-specific prelaunch hash snapshot. The modify baseline hashes are therefore explicitly reconstructed from the identical fixed v2 source and preserved analysis fixture baseline, not claimed to be a modify-specific previously saved hash record. Protected-file equality, independent five-test success, current SYSTEM receipt and native editor stream evidence are physically verified. Future launcher runs now persist baseline hashes before launching the editor.

No existing VSCode user configuration was changed; product source, provider configuration defaults and candidate files were not modified by this harness task. The owned harness exercises saved local files and one ACP session per task. Existing user plugins, unsaved buffers, editor-native file editing, screenshots, other editors and any cloud hosting are outside this acceptance.

## Reproducible harness

All scripts are below 300 lines and live only in this S16 evidence folder:

- `run_vscode_acp.py`: creates isolated fixture/configuration/development extension and launches the actual editor only with explicit `--run`.
- `vscode_acp_extension_tests.js`: actual extension-host runner and live editor rendering.
- `vscode_acp_worker.py`: official Python ACP bridge, durable readback and agent shutdown.
- `collect_vscode_acp.py`: offline native-log/hash/unittest/receipt checks, without provider calls.
- `audit_vscode_artifacts.py`: offline usage and exact-secret artifact scan.

Prepare only: `candidate/.venv/Scripts/python.exe docs/next-version/s16/run_vscode_acp.py analysis`.
Authorized live execution: append `--run`, or select `modify --run`. These explicit live commands call the configured provider; offline collectors do not.
