# Latest afbd provenance independent supervision: PASS_PROVENANCE_SCOPE

Candidate `afbd8f3ee7163cb71959a6af0bdfb77b6c082cdd`, tree `c93d1b408938ddc3f368ad2e969d549dea61d58d`. Independent Git blob audit confirms all **1,665** source ZIP files match the exact commit bytes. Runtime wheel SHA256 `5a268b0941fbfe4ce047e0c6d7fec26c3515da893387c55662a107ae0b02e8e9`; development wheel SHA256 `48d551c5ebfddfcdfed38463b193f35b769a33503ed9ff27702d11d574af9464`.

Every runtime source member (632 including629Python) and development source member (40Python) matches a current Git blob. All wheel RECORD digests/sizes validate, including generated metadata. New wheels are independently checked against Git rather than assuming the old runtime wheel is reusable. Both3.10 and3.13 clean-install logs physically reference the new afbd wheel, exit0, installation of runtime/development packages, pip-check success and bounded offline benchmark PASS/cleanup. Offline scripted usage is not Provider evidence.

Candidate authentication has no diff from the S2 baseline. Main's protected personal authentication diff hash remains `7cbeb87002763fbe5de0bf33a4fd68193af6d66e5340be75f2a1edeb776befca`. Read-only current network inventory confirms preserved loopback127.0.0.1:8787 listener/PID66688; the reviewer did not stop/restart it.

S15 upgrade/backup rollback evidence remains historical PASS at treec3a3094d4b557926d081a4f2accdf20caebb47a8. Exact Git diff from that tree to afbd for Sessions and Workspace is empty, so migration/repository/workspace code exercised by the probe is unchanged. This is code-equivalence review, not a new user-database migration or a fresh upgrade run. Real user databases and original Host were not migrated.

Independent command and file/log hashes are recorded in JSON; actual exit0. Scope is source/build/install/protection/upgrade-equivalence only. This does not approve release, overwrite previous failures, resolve real v5 child/source quality blockers or declare S16 overall PASS.
