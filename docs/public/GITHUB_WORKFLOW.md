# Git history and milestone delivery

## Public boundary

The public repository is opt-in, not a backup of an entire research machine.
The executable allowlist is in `tools/check_public_tree.py`. The M02 tooling
extension adds exactly three approved paths: the draft instrumentation module,
its synthetic test module, and its public guide. It does not release real cases,
gold, raw logs or application source; see [the instrument guide](B210_INSTRUMENT.md).
`.gitignore` prevents ordinary accidental staging. The checker inspects staged
modes and blobs as well as the corresponding tracked worktree files; sanitizing
a worktree file without restaging it does not sanitize the index. It rejects
files outside the allowlist, symlinks, oversized files, and selected
high-confidence credential formats, and fails closed on uninspectable state or
an empty index. It does not scan untracked or ignored files or commit history.
These controls reduce risk; they are not a complete secret scanner, filesystem
sandbox, or proof of permission to publish.
The PR diff and new reachable history must still be inspected before publication.

Original evaluation inputs, gold answers, reviews, research documentation, raw
operator evidence, model weights, caches, virtual environments, snapshots and
archives are withheld. Do not sanitize originals in place. A future public release
requires an explicit scope decision, safe derivatives if needed, provenance, and
an intentional allowlist change. Public Git history covers only released files;
local withheld edits are not falsely described as GitHub-versioned.

## Branches and commits

- Preserve the pre-existing `main` history and LICENSE. Do not push directly to
  `main`; deliver through a gated PR and leave merging to the owner.
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

For this Linux checkout, Git 2.34.1 failed before pipeline creation because it
lacks the `symbolic-ref --no-recurse` option used by no-mistakes v1.106.1. The
operator authorized user-local Git 2.56.1, verified against the official
kernel.org SHA-256 manifest and built without changing system Git. A release
signing-key verification was not performed; do not describe the checksum check
as a verified GPG signature. The source archive SHA-256 is
`f0b0bf89264b9bd2e1c640363e7529643989da76281600473c50745cd34a607f`.
Use the gate-specific environment before initializing or restarting its daemon:

```sh
export NM_HOME="$HOME/.no-mistakes-luna"
export PATH="$HOME/.local/opt/git-no-mistakes/2.56.1/bin:$PATH"
git --version
```

The standard `/usr/bin/git` and research environments remain unchanged. This is
a tested compatible version, not a claim about the oldest supported Git version.
Restart only the isolated gate daemon after changing its toolchain environment.
A managed daemon resolves its environment from the login shell: exporting PATH in
the caller alone did not update the daemon on this machine. The operator's local
Luna service therefore has its own ZDOTDIR startup wrappers, which source the
normal user profiles and prepend the dedicated Git directory at the end of login.
The instance-specific systemd user drop-in exports that ZDOTDIR and NM_HOME.
Those service files remain local; no existing user shell profiles or other
no-mistakes instances are changed. Verify the service's effective toolchain, not
only the version printed by a client-side doctor invocation.

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
At bootstrap entry main had no such file. The bootstrap has since been merged;
subsequent gates use the policy from fetched main. For the current shipped scope,
see [README](../../README.md#current-public-scope). Operator-owned local safety
settings and additive checks supplemented the first gate; the feature branch
could not make its own policy trusted before that merge.

The committed workflow runs stdlib unit and CLI tests, then checks the tracked
publication surface. It intentionally does not discover all historical test
scripts: importing a remote inference script can perform network requests.
Application offline regression coverage is a separate subsequent milestone task.
The approved instrumentation extension also exercises synthetic subprocess
success/failure/timeout and draft integrity offline. Never describe these
checks as RAG, GPU, inference, or clinical tests.

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
