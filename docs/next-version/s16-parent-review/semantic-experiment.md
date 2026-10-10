# Development-side semantic experiment

This local experiment is separate from production S16 acceptance. It does not
delegate agents, execute model tools, or claim that the model ran tests.

Three cases are frozen before any provider call: the saved faulty advisory on
the original list-copy implementation; a correct advisory on identity return;
and a partly correct advisory on trim/filter/sort. The implementation variant
changes only `names.py`; all four source bodies are hash checked or rehashed.
The oracle and counterexamples never enter request assembly.

A exposes the advisory in phase 1; B exposes it in phase 2. Both arms have two
requests with identical system/output contracts, model `glm-5.3-flash`, effort
`medium`, and 4096 output tokens per request (8192 per pair). Source text and
physical line numbering are identical within each pair. Phase 2 includes the
actual phase 1 response. No history, summaries, notes, or arbitrary context are
accepted by the request audit. Save the actual final serialized wire body and
audit it before sending, since a planned body alone does not prove isolation.
Input token use necessarily differs because early exposure includes additional
text; record input/output/reasoning usage and unknown usage without estimating
it as zero. The real runner uses a common 60000 token accounting allowance per
pair: reserve the complete actual prepared body's UTF-8 byte count plus 4096
before each send. This is a conservative local reservation, not an exact GLM
token count; unknown usage retains it. Reported usage can increase the charge
but never reduces the reserved allowance. An equal
maximum output allowance is not proof of equal actual total cost.

Minimum real experiment: 3 cases x 2 arms x 2 phases = 12 requests. Transport
retry is disabled. Failure stops the affected pair, preserves all outputs and
request/error records, and is not replaced. Isolation/source/budget failure
stops before sending. Provider request timeout and total-cost ceiling are set
by the authorized real runner; budget exhaustion never grants another request.
The failed pair stops; the runner continues the next pair in the frozen
case order, with A then B. Maximum 12 external sends and no replacement calls.
Use a fresh output directory for each attempt. Report every attempted pair,
including failures, malformed JSON, and explicit unknowns. Small sample results
show only these cases, not a statistically established model improvement.

`semantic_oracle.py` executes source tests and counterexamples only in developer
namespaces. Its compliance checks use distinct unsorted names for order and
duplicates. No-mutation compares input before/after; distinct-object return is
an independent observation and is not required by this contract.

The five tests have different discrimination limits:

| Test | Detects on its specific input | Cannot establish |
| --- | --- | --- |
| empty | Nonempty/None return or exception on empty input | Any nonempty behavior, order, duplicates, mutation |
| trim | Missing trim; reversal of its two distinct resulting names | Sorting, since its expected names are already sorted; all strings |
| blanks | Failure to omit its three blank values | Filtering across arbitrary nonblank inputs |
| duplicates | Loss of its duplicate; missing trim | Sorting or reversal of equal resulting names; general order |
| no_mutation | Modification of its selected input | Fresh-object return, general mutation safety, correct output |

The sorted-clean variant passes all five tests and still violates order on
distinct unsorted input. This is a developer observation, not model execution.
Decision scoring labels false acceptance, false rejection, and unknown/missing
separately. Structured fields do not establish correct citations, accurate
test-discrimination prose, or sound comparison; review these against sources
and preserve the complete text. Do not promote delivery shape to `verified`.

Offline commands from this directory:

```text
python -m unittest test_semantic_harness -v
python semantic_harness.py
python semantic_oracle.py
python run_semantic_experiment.py
```

Explicit authorized real runner: `python run_semantic_experiment.py --execute
--output <new-directory>`. It prepares requests with the existing profile and
`OpenAIChatClient`, sets transport `max_retries=0`, audits/saves exact HTTP body
bytes immediately before send, and captures SSE/events including raw usage
details (reasoning tokens when present). It persists no headers or URLs and
records only exception types. Raw body credential detection blocks transmission.
Default invocation does not read credentials or invoke a provider.
