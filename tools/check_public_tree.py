#!/usr/bin/env python3
"""Validate the deliberately small public tooling Git-index contract.

This is an allowlist/credential-format check, not a complete security scanner.
Only Git-tracked files are read; private ignored workspace data is not inspected.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Iterable

ALLOWED_PATHS = frozenset({
    '.gitignore', '.gitattributes', '.no-mistakes.yaml', 'README.md', 'LICENSE',
    '.github/workflows/quality.yml', 'docs/public/GITHUB_WORKFLOW.md',
    'tools/check_public_tree.py', 'tests/public/test_public_tree.py',
    'tools/b210_instrument.py', 'tests/public/test_b210_instrument.py',
    'docs/public/B210_INSTRUMENT.md', 'docs/public/B210_LIVE.md',
    'tools/b210_live.py', 'tests/public/test_b210_live.py',
})
MAX_FILE_BYTES = 256 * 1024
CREDENTIAL_PATTERNS = (
    re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    re.compile(rb'\bgh[pousr]_[A-Za-z0-9]{30,}\b'),
    re.compile(rb'\bgithub_pat_[A-Za-z0-9_]{40,}\b'),
)


@dataclass(frozen=True)
class Violation:
    path: str
    code: str


def audit_files(root: Path, paths: Iterable[str]) -> list[Violation]:
    """Evaluate exact release paths and owned file bytes without following links."""
    root = root.resolve()
    problems: list[Violation] = []
    for name in sorted(set(paths)):
        path = PurePosixPath(name)
        if (not name or '\\' in name or '\x00' in name or path.is_absolute()
                or '..' in path.parts or path.as_posix() != name):
            problems.append(Violation(name, 'invalid-path'))
            continue
        if name not in ALLOWED_PATHS:
            problems.append(Violation(name, 'not-allowlisted'))
            continue
        file = root / name
        if any(p.is_symlink() for p in [file, *list(file.parents)[:-1]]):
            problems.append(Violation(name, 'symlink'))
            continue
        if not file.is_file():
            problems.append(Violation(name, 'missing-or-not-file'))
            continue
        if file.stat().st_size > MAX_FILE_BYTES:
            problems.append(Violation(name, 'oversize'))
            continue
        data = file.read_bytes()
        if any(pattern.search(data) for pattern in CREDENTIAL_PATTERNS):
            problems.append(Violation(name, 'credential-pattern'))
    return problems


def inspect_index(root: Path) -> tuple[list[str], list[Violation]]:
    """Inspect staged modes and blobs, not just possibly-restaged worktree bytes."""
    result = subprocess.run(
        ['git', '-C', str(root), 'ls-files', '--stage', '-z'],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    paths: list[str] = []
    problems: list[Violation] = []
    for record in result.stdout.split(b'\0'):
        if not record:
            continue
        header, raw_name = record.split(b'\t', 1)
        mode, object_id, stage = header.decode('ascii').split()
        name = raw_name.decode('utf-8', errors='strict')
        paths.append(name)
        if stage != '0':
            problems.append(Violation(name, 'unmerged-index'))
            continue
        if name not in ALLOWED_PATHS:
            # The path audit rejects it without reading private staged content.
            continue
        if mode == '120000':
            problems.append(Violation(name, 'symlink'))
            continue
        if mode not in ('100644', '100755'):
            problems.append(Violation(name, 'unsupported-index-mode'))
            continue
        size = subprocess.run(
            ['git', '-C', str(root), 'cat-file', '-s', object_id],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if int(size.stdout) > MAX_FILE_BYTES:
            problems.append(Violation(name, 'oversize'))
            continue
        blob = subprocess.run(
            ['git', '-C', str(root), 'cat-file', 'blob', object_id],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout
        if any(pattern.search(blob) for pattern in CREDENTIAL_PATTERNS):
            problems.append(Violation(name, 'credential-pattern'))
    return paths, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve(strict=True)
        paths, staged_problems = inspect_index(root)
        if not paths:
            print('ERROR: empty Git index is not a validated publication', file=sys.stderr)
            return 2
        problems = sorted(set(audit_files(root, paths) + staged_problems),
                          key=lambda violation: (violation.path, violation.code))
    except (OSError, UnicodeError, ValueError, subprocess.CalledProcessError) as error:
        print(f'ERROR: cannot validate tracked publication ({type(error).__name__})',
              file=sys.stderr)
        return 2
    for problem in problems:
        # Never print matched secrets or private file contents.
        print(f'REJECT {problem.code}: {problem.path}', file=sys.stderr)
    if problems:
        return 1
    print(f'PASS: {len(paths)} tracked files satisfy the public tooling publication contract')
    print('Scope: public tooling/fixtures only; not exhaustive secret scanning or RAG validation')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
