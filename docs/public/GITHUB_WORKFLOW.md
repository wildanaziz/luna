# Git history and milestone delivery

## Public boundary

The public repository is opt-in, not a backup of an entire research machine.
The bootstrap's executable allowlist is in `tools/check_public_tree.py`.
`.gitignore` prevents ordinary accidental staging, and the checker rejects
force-added files outside that allowlist, symlinks, oversized files, and selected
high-confidence credential formats. These controls reduce risk; they are not a
complete secret scanner, filesystem sandbox, or proof of permission to publish.
The PR diff and new reachable history must still be inspected before publication.

Original evaluation inputs, gold answers, reviews, research documentation, raw
operator evidence, model weights, caches, virtual environments, snapshots and
archives are withheld. Do not sanitize originals in place. A future public release
requires an explicit scope decision, safe derivatives if needed, provenance, and
an intentional allowlist change. Public Git history covers only released files;
local withheld edits are not falsely described as GitHub-versioned.

## Branches and commits

- Preserve the pre-existing `main` history and LICENSE.
- Bootstrap: `chore/bootstrap-quality-gate`.
- Research work: `milestone/m02-b210`, `milestone/m03-robustness`, and corresponding
  milestone branches as work begins; do not create empty branches for all stages.
- Commit scoped additions, changes, renames and deletions with a meaningful reason.
  An initial import records current state, not reconstructed historical edits.
- Use a merge commit for milestone PRs when preserving the individual commit
  graph is required. Do not squash or rewrite published research provenance by
  default. A pipeline-internal rebase can still change feature commit identities;
  archive relevant submitted/resulting SHAs and inspect them.
- Each experimental record should identify the code commit and separate external
  artifact fingerprints. Tests of fixtures are not experimental runs.

## Local gate and GitHub checks

For a committed non-main branch after `no-mistakes init --skip-user-skill`:

```sh
git push no-mistakes <branch>
```

An automated driver can use `no-mistakes axi run --intent-file <public-intent-file>
--no-publish-intent` to drive the same validation and delivery pipeline. Supply
only a public-safe task description, not private conversation transcripts.
This checkout uses the isolated operator-owned root
`NM_HOME="$HOME/.no-mistakes-luna"`; export that value for both initialization and
later pushes/AXI commands so they use the same gate state. The standard
`~/.no-mistakes/config.yaml` is not changed. Do not use both entry points to start duplicate runs.

The gate performs review, test, documentation, lint, delivery and CI observation;
it is not merely a test runner and can generate changes. Disable transcript
inference and automatic evidence/media publication. Do not skip or approve over
failed checks, silently weaken tests, change the scientific baseline/gold, or
merge automatically. Escalate intent-changing findings to the owner. Even with
zero follow-up auto-fix attempts, an initial documentation pass can edit files.
`protected_paths` is a staging guard, not a sandbox or protection against every
already-committed change; inspect the resulting diff and rerun checks.

## One-time bootstrap trust boundary

In no-mistakes v1.106.1, commands, agent selection, protected paths and several
other controls are trusted from the fetched default branch. This PR cannot make
its own controls trusted simply by adding `.no-mistakes.yaml` on a feature branch.
The existing main has no such file. The bootstrap therefore contains governance
only, no application source or withheld data. Use operator-owned local safety
settings and additive checks for the first gate. The trusted repository policy
becomes available only after a human merges the bootstrap.

The committed workflow runs stdlib unit and CLI tests, then checks the tracked
publication surface. It intentionally does not discover all historical test
scripts: importing a remote inference script can perform network requests.
Application offline regression coverage is a separate subsequent milestone task.
Never describe these bootstrap checks as RAG, GPU, inference, or clinical tests.

## Main protection and human merge

A configuration file alone does not protect GitHub main. After checks are
registered, require a PR, the actual successful GitHub status check, conversation
resolution, and no force pushes or branch deletion, including administrators
where supported. Keep the manual merge decision with the owner. Do not require
an unavailable independent reviewer in a single-researcher workflow or call
self-review independent review.

The local no-mistakes remote does not prevent `git push origin`. GitHub required
checks protect the checks they actually enforce; a successful test job alone does
not certify a no-mistakes run. If enforcing no-mistakes provenance on the server,
install and require its official attestation check in a separately reviewed
change. Do not claim it is installed until verified. Preserve the PR URL, run ID,
submitted and pipeline-result SHAs, failures/fixes, and CI conclusions in a safe
release record; keep private pipeline logs local.
