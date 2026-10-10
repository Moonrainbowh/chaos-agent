# Production regression evidence

Workspace: `C:\Users\Windows11\.codex\worktrees\s16-parent-review\chaos-16-agent`.
Interpreter: `.venv/Scripts/python.exe`, Python 3.13.7.
All model responses in these tests are scripted at the HTTP transport boundary;
production task, context, provider serialization, dispatcher, policy, child factory,
Sessions and completion paths execute normally. These tests do not establish semantic
correctness of a real model.

## RED before the production patch

Command:

```text
.venv/Scripts/python.exe -m unittest tests.test_parent_source_review.ParentSourceReviewTests.test_source_delegate_enters_two_isolated_parent_requests
```

Actual tool output: `Ran 1 test in 9.545s`, `FAILED (failures=1)`.
The failure was `AssertionError: 3 not greater than or equal to 4`:
the actual parent sent disclosure, delegation and one answer request, with no
separate independent and comparison requests.

## Local verification

- The first four production regressions passed: `Ran 4 tests in 33.368s`, `OK`.
- Production boundary expansion plus the existing corrected child read case passed:
  `Ran 9 tests in 83.377s`, `OK`.
- The existing four-file frozen fixture, updated to supply both required parent
  responses, passed individually: `Ran 1 test in 10.416s`, `OK`.
- Final production/schema command:
  `.venv/Scripts/python.exe -m unittest tests.test_parent_source_review src.code_agent.core.tests.test_parent_review_contract`
  returned `Ran 16 tests in 78.538s`, `OK`.
- Combined source regression rerun:
  `.venv/Scripts/python.exe -m unittest tests.test_parent_source_review tests.test_s16_source_completion tests.test_source_completion_contract`
  returned `Ran 43 tests in 234.480s`, `OK`. This process started before the
  additional child-objective isolation case was added; its eight parent cases
  plus the original 35 source regressions passed. The additional case and final
  schema changes are covered by the separate 16-test command above.

The final production module checks actual serialized independent/comparison inputs,
model-authored child objective isolation, isolated initial repair, one global repair,
full advisory beyond the distilled tool summary limit, real source disappearance,
shared hard budget with zero additional Host read/provider request, the soft-lease
completion entry, original budget charges and rendered unverified delivery.
Internal JSON must be absent from both live output and persisted visible messages/events.

Earlier combined execution found two outdated scripted-parent fixtures that provided
only one answer request; they raised `unexpected extra model request`. Their scripts
were updated for the new production stages rather than weakening the parent gate.
The original child read/receipt assertions remain in place. Final whole-project results
are maintained separately by the parent agent.

After the production freeze, the parent agent corrected the remaining-work description
for a budget pause after an already delivered review, without changing budget priority.
The Core contract module then passed `Ran 8 tests in 0.004s`, `OK`; the whole-project
Core suite later included that version and passed 211 tests. These counts overlap the
earlier targeted runs and must not be added as a unique-test total.
