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
  --output /private/path/new-draft --seed 20261119
python3 tools/b210_instrument.py verify /private/path/new-draft
```

Cases have a nonempty `cases` list of `{id, question, label}` objects; gold has an
`items` list with exactly one matching `{id, stratum}` per case. Identifiers must
be unique safe filename components, questions/labels nonempty, and strata must
match labels. These are structural checks, not a scientific or medical gold
review. No target number or clinical strata are inferred from generic fixtures.

The draft snapshots the original input bytes unchanged and stores the actual
shuffled ID list, seed, `python.random.Random.shuffle` algorithm, Python version,
SHA-256 fingerprints, and the planned count of two primary attempts per case.
Use the same saved order for retrieval first and generation second. The seed
alone is not a cross-version ordering guarantee; archive the list and version.
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
replace a primary attempt. A killed supervisor can leave `STARTING` in its ledger;
that is incomplete evidence, not success or a reason to exclude the attempt.

Timeout/interrupt cleanup targets the worker's own POSIX session/process group,
not a shared model server. An optional mapping of small artifact paths to expected
SHA-256 values is recorded and checked before/after an attempt; a mismatch pauses.
This is not continuous monitoring or remote process/model attestation. Process
elapsed timestamps are diagnostic, not decoding throughput, TTFT or GPU telemetry.

Exactly one JSON object with a recognized stage-appropriate status is required
in each raw log. Recognition of existing RAG status names is only a structural
contract: the RAG application has not been run to validate live integration.
A worker's `critical_suspected: true` pauses; no clinical detector is implemented.
Generation pauses for review by default. A supplied review callback may explicitly
return `True` to continue, but this is a trusted integration hook, not a clinical
approval or automatic gold scorer. Callback failures pause; user interruption is
recorded. Synthetic callback approval in tests must not be described as medical
review. A technically successful answer can still fail every quality criterion.

A later authorized live adapter must connect the saved order/configuration,
actual RAG arguments/IDs/raw metadata, human review, preflight-derived timeout,
freeze/sign-off, execution permission, resume provenance and diagnostic retry
policy. This release intentionally does not provide a live/resume/retry command.
Do not claim all M02 run-readiness work is closed from these fixtures alone.

## Offline tests and publication boundary

```sh
python3 -m unittest discover -s tests/public -p 'test_*.py' -v
python3 tools/check_public_tree.py
```

Tests exercise draft mapping, exact snapshots, repeatable ordering, tampering,
overwrite refusal, CLI behavior and real synthetic subprocess success, launch
failure, invalid/missing raw output, timeout with partial stdout, interruption,
artifact changes and conservative review/critical pauses. They never use withheld
case text or original-checkout application imports as missing test material.

The exact added public paths are `tools/b210_instrument.py`,
`tests/public/test_b210_instrument.py`, and this guide. Existing privacy checks
remain. Real cases, gold, draft bundles, runtime logs, research documents and
review records stay private/ignored and are not published with this code.
See [delivery and public scope](GITHUB_WORKFLOW.md).
