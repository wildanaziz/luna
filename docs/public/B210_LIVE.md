# V06-fix research adapter

`tools/b210_live.py` is a library for an authorized research operator. It has no live CLI. Not for patient care or clinical use. The public tests run synthetic workers only and make no network calls.

The adapter invokes V06-fix directly rather than using the older batch runner. It uses the verified draft's saved order for retrieval first, then generation. It hashes the draft, the source and the index before spawning a worker. Only the question is passed from the case/gold inputs; gold is read locally by the draft verifier. The existing offline supervisor still has its original fixture status contract.

Each attempt has a separate directory with original JSONL, combined stdout/stderr, and an entry in `attempts.json`. The ledger separates the exit status, original RAG status, observed display behavior and unassessed quality. An abstention can be a technically completed attempt, not a quality pass. `stop_ordinal` points to the interrupted or unresolved attempt; later rows remain `NOT_RUN`.

For generation, the adapter persists `review_required` before handing the raw path and row identity to the supplied reviewer callback. An identified human's structured `CONTINUE` with a rationale can advance the same process. `PAUSE`, `UNRESOLVED`, absent review and invalid replies stop it. The callback is trusted integration code, not an automated or clinical reviewer. Never wire a callback that issues unconditional approval for actual research output.

A `Receipt` records externally granted preflight, freeze and execution references; filling its fields does not create permission. The caller must verify those records, the runtime session and the approved execution window. The explicit outer timeout must come from preflight evidence, not a fixture. Hugging Face offline flags are not a network sandbox. A local subprocess timeout does not prove remote inference has stopped. Do not retry or restart based on timeout alone. There is no automatic or cross-process resume. Preserve the stopped ledger for an operator's subsequent decision; do not restart the full primary batch in a fresh directory as a substitute for resuming or replace a primary with diagnostic output.

Run the synthetic regression suite from the repository root:

```sh
python3 -B -m unittest discover -s tests/public -p 'test_*.py'
```
