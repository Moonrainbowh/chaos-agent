# Frozen compatibility wheel clean-install results

Both actual checks exited 0, using fresh temporary environments outside the checkout:

- Python 3.13: `compat-clean-install313.log` / `.exit`.
- Python 3.10: `compat-clean-install310.log` / `.exit`.

The runtime and development wheels are the frozen `4ed1a6a9` artifacts whose SHA256 values are in `compat-source-manifest.json`; candidate `2810fcbd` differs only in three root test files. This installation evidence does not certify an unbuilt future candidate tree.

The standard `scripts/check_wheel_install.py` checker installed from the hash-checked owned wheelhouse without an index; it verified pip consistency, four CLI entry-point help commands, packaged resources, isolated history/task results without a Provider, development-package separation, v1/v2 offline references, and production Host arm A's fixed offline fixture. The isolated probe uses `-I` and asserts imported product files belong to the new environment. Runtime metadata correctly adds `async-timeout==5.0.1` only below Python 3.11; the 3.10 installation log includes it.

`compat-install-cache.json` binds all 53 cached wheel names and SHA256 values. Existing S15 cache files were checked and copied to a new owned cache without changing them. Only the locked 6,233-byte backport wheel was downloaded, and its SHA256 matched `uv.lock`. The first download attempt used the candidate environment, which has no pip; its log is preserved as `compat-backport-download-no-pip.log`. The successful download used the base interpreter without changing any interpreter's installed packages. Certificate environment overrides were removed only from that download subprocess.

These are installation and deterministic offline results. Real-model CLI and actual editor evidence are separate; neither this check nor its ScriptModel output is real-model validation. Final S16 CI and independent supervision remain incomplete.
