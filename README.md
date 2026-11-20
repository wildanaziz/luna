# Luna

Research workspace for an Indonesian health-information RAG prototype on a
resource-constrained device. The intended use is understanding health information
and preparing a consultation, not individual diagnosis, treatment, or replacing
health professionals.

## Current public scope

The public tree contains release-policy tooling, offline tests, CI, public
workflow documentation, [draft preparation/subprocess instrumentation](docs/public/B210_INSTRUMENT.md),
and a [research-only V06-fix adapter](docs/public/B210_INSTRUMENT.md#v06-fix-research-adapter).
The instrumentation and adapter are tested with synthetic workers, not the medical application.
The RAG application and research experiments are **not** shipped or evaluated here. See the [public boundary](docs/public/GITHUB_WORKFLOW.md#public-boundary)
for withheld materials and future release requirements.

Run the public tooling checks from the repository root:

```sh
python3 -m unittest discover -s tests/public -p 'test_*.py' -v
python3 tools/check_public_tree.py
```

Python 3.10+ and Git are sufficient. No model, GPU, network service, or third-party
Python dependency is needed for these checks. A pass demonstrates this public
publication and offline fixture behavior, **not** model quality, clinical safety, or milestone
experiment completion.

## Delivery workflow

Follow the [GitHub workflow and bootstrap limitations](docs/public/GITHUB_WORKFLOW.md)
for milestone branches, commits, gated delivery, and the owner's manual merge.
The existing [LICENSE](LICENSE) is retained unchanged; it does not grant rights
to third-party or withheld research materials.
