# Luna

Research workspace for an Indonesian health-information RAG prototype on a
resource-constrained device. The intended use is understanding health information
and preparing a consultation, not individual diagnosis, treatment, or replacing
health professionals.

## Current public scope

This first PR bootstraps version control and a quality gate. It publishes only
release-policy tooling, offline tests, CI, and public workflow documentation.
The RAG application and research experiments are **not** shipped or evaluated by
this bootstrap. Withheld evaluation questions, gold answers, reviews, original
research documents, operator evidence, models, dependencies, vendor snapshots,
and runtime artifacts remain outside this public history.

Run the bootstrap checks from the repository root:

```sh
python3 -m unittest discover -s tests/public -p 'test_*.py' -v
python3 tools/check_public_tree.py
```

Python 3.10+ and Git are sufficient. No model, GPU, network service, or third-party
Python dependency is needed for these checks. A pass demonstrates this public
publication contract, **not** model quality, clinical safety, or milestone
experiment completion.

## Delivery workflow

Use milestone branches, meaningful commits, a `no-mistakes` gate, GitHub checks,
and a manually reviewed pull request. Start with the dedicated
`chore/bootstrap-quality-gate` branch; later work uses branches such as
`milestone/m02-b210`. Do not push directly to `main` or auto-merge.

See [GitHub workflow and bootstrap limitations](docs/public/GITHUB_WORKFLOW.md).
The existing [LICENSE](LICENSE) is retained unchanged; it does not grant rights
to third-party or withheld research materials.
