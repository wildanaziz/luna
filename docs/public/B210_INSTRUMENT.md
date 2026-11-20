# Draft preparation and offline subprocess instrumentation

This release adds a standard-library preparation CLI and a POSIX supervision API.
Its tests use invented identifiers/questions and temporary Python workers only.
It does **not** run or validate the medical RAG, freeze an experiment, choose a
runtime timeout, certify clinical safety, or grant execution permission.

## Preparation CLI

Python 3.10+ is sufficient. Supply private, operator-owned input paths and a new
output directory whose parent already exists:

```sh
python3 tools/b210_instrument.py prepare \
  --cases /private/path/cases.json --gold /private/path/gold.json \
  --output /private/path/new-draft
python3 tools/b210_instrument.py verify /private/path/new-draft
```

Cases have a nonempty `cases` list of `{id, question, label}` objects; gold has an
`items` list with exactly one matching `{id, stratum}` per case. Identifiers must
be unique safe filename components, questions/labels nonempty, and strata must
match labels. These are structural checks, not a scientific or medical gold
review. No target number or clinical strata are inferred from generic fixtures.

The draft snapshots the original input bytes unchanged and stores the actual
shuffled ID list, fixed seed `20261119`, `python.random.Random.shuffle` algorithm,
Python version, SHA-256 fingerprints, and the planned count of two primary
attempts per case.
Use the same saved order for retrieval first and generation second. Alternate
seeds are not supported by the API, CLI or bundle verifier. The seed alone is not
a cross-version ordering guarantee; archive the list and version.
This ordering does not make cases independent or control a performance comparison.

An optional repeated `--artifact /path/to/file` records local file hashes. Do not
hash large model files or remote mounts without the separate resource/operator
permission appropriate to that work. Preparation imports no model/runtime packages,
spawns no worker and contacts no endpoint. It leaves `freeze_approved` and
`execution_authorized` false, state `DRAFT_NOT_FROZEN`, preflight `NOT_PERFORMED`,
and whole-process timeout unset. There is deliberately no freeze or execution CLI.
`verify` checks snapshot/order/artifact consistency, not authorization or source
truth. Hashes are not a signature: someone able to edit a bundle can rewrite its
manifest too. The draft is not a signed, tamper-proof or immutable acceptance.
Existing output directories are refused, even when empty. Partial creation on an
I/O failure is not a valid bundle; preserve it for inspection and use a new path.

## Supervision API and its limited evidence

`Task(case_id, stage, argv)` and `supervise_tasks(tasks, output, timeout, ...)`
are tested independently of real experiment inputs. The API executes trusted
argument vectors without a shell, substitutes the exact `{raw_log}` token with
a per-attempt JSONL path, and requires an explicit positive finite timeout.
The caller must own and authorize the commands: this is **not a sandbox or an
offline/network restriction**. Do not point it at the medical runtime under this
release's fixture-only execution permission.

Before starting it validates task IDs, unique case/stage identities, and ordering
(no retrieval after generation). It creates a new private output directory and
an atomic ledger containing all planned entries, initially `NOT_RUN`. It streams
combined stdout/stderr directly to disk, records command/timestamps/exit/status,
and pauses on launch/nonzero exit/timeout/interruption/invalid or missing raw
JSONL. It does not retry, restart shared services, overwrite outputs or silently
replace a primary attempt. Interruption across prelaunch checks, worker waiting,
log reading, review and final integrity checks records a paused/interrupted ledger
before re-raising; `worker_launched` distinguishes an unlaunched attempt from an
executed worker. A killed supervisor can leave `STARTING` in its ledger;
that is incomplete evidence, not success or a reason to exclude the attempt.

Timeout/interrupt cleanup targets the worker's own POSIX session/process group,
not a shared model server. An optional mapping of small artifact paths to expected
SHA-256 values is recorded and checked before/after an attempt; a mismatch pauses.
This is not continuous monitoring or remote process/model attestation. Process
elapsed timestamps are diagnostic, not decoding throughput, TTFT or GPU telemetry.

Exactly one JSON object is required in each raw log. Only `retrieval_only` for
retrieval and `success` for generation are accepted. These are synthetic prototype
contracts, not claims of compatibility with live RAG or safety-status mappings.
A worker's `critical_suspected: true` pauses; no clinical detector is implemented.
Generation pauses for review by default. A supplied review callback may explicitly
return `True` to continue, but this is a trusted integration hook, not a clinical
approval or automatic gold scorer. Callback failures pause; user interruption is
recorded. Synthetic callback approval in tests must not be described as medical
review. A technically successful answer can still fail every quality criterion.

For the separate V06-fix integration, see the [research adapter](#v06-fix-research-adapter).
The fixture API above retains its original status and boolean callback contract.

## V06-fix research adapter

`tools/b210_live.py` is a POSIX library for a separately authorized research
operator, not a live CLI or permission to execute research. It is not for patient
care or clinical use. Public tests exercise synthetic workers only, without
network calls; they do not establish compatibility with the unchanged real worker.

The adapter invokes V06-fix directly rather than using the older batch runner.
It verifies the draft and uses its saved order for retrieval first, then generation.
The draft must pin the worker source and all required index artifacts. The draft,
its snapshots and pinned artifacts are hash-checked before and after attempts.
Only the question is passed from case/gold inputs to the worker; gold is read
locally by the draft verifier. The fixed invocation and expected raw profile are
owned by `tools/b210_live.py`, not by the generic fixture supervisor.

Each attempt has a separate directory with unchanged raw JSONL and combined
stdout/stderr, plus an entry in `attempts.json`. The ledger separates exit status,
original worker status, observed display behavior and unassessed research quality.
An abstention can be technically completed without being a quality pass. Worker
failures, timeout, invalid/missing records or identity/profile mismatches, artifact
drift and pending review stop progression. `stop_ordinal` identifies the stopped
attempt; later rows remain `NOT_RUN`.

For generation, the adapter persists `review_required` before handing the raw path
and row identity to the supplied reviewer callback. An identified human's structured
`Review` with `CONTINUE` and a rationale can advance the same process. `PAUSE`,
`UNRESOLVED`, absent review and invalid replies stop it. The callback is trusted
integration code, not an automated or clinical reviewer, and the adapter cannot
authenticate the human behind it. Never wire unconditional approval for actual
research output. Raw and input hashes are checked again after review.

A `Receipt` records externally granted preflight, freeze and execution references;
filling its fields does not create permission or change the bundle's draft state.
The caller must verify those records, the runtime session and approved execution
window. The explicit outer timeout must come from preflight evidence, not a fixture.
The endpoint is restricted to explicit IPv4 loopback HTTP, but Hugging Face offline
flags are not a network sandbox. A local subprocess timeout does not prove remote
inference has stopped. Do not retry or restart based on timeout alone.

There is no automatic retry, restart or cross-process resume. Existing output
directories are refused. Preserve the stopped ledger for an operator's subsequent
decision; do not restart the full primary batch in a fresh directory as a substitute
for resuming or replace a primary with diagnostic output. Do not claim all M02
run-readiness work is closed from these fixtures alone.

## Offline tests and publication boundary

```sh
python3 -m unittest discover -s tests/public -p 'test_*.py' -v
python3 tools/check_public_tree.py
```

Tests exercise draft mapping, exact snapshots, repeatable ordering, tampering,
overwrite refusal, CLI behavior and real synthetic subprocess success, launch
failure, invalid/missing raw output, timeout with partial stdout, interruption,
artifact changes and conservative review/critical pauses. Adapter tests additionally
exercise saved ordering, raw provenance, observed display classifications, structured
review and identity/profile rejection. They never use withheld case text or
original-checkout application imports as missing test material.

The executable publication allowlist is in `tools/check_public_tree.py`.
Draft bundles and supervision outputs stay private/ignored; they are not published
with this code. See [the public boundary and delivery policy](GITHUB_WORKFLOW.md#public-boundary).
