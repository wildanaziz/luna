"""Behavioral tests of the user-approved public-release contract, not RAG.

Oracle: only the named tooling files may be published; withheld research,
credentials, links, oversized files and uninspectable Git state must fail closed.
Temporary Git repos exercise the real index/ignore consumer and CLI, offline.
"""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from tools.check_public_tree import audit_files

SOURCE_ROOT = Path(__file__).resolve().parents[2]
CHECKER = SOURCE_ROOT / 'tools/check_public_tree.py'


class PublicationContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def put(self, name, data=b'public documentation\n'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def codes(self, paths):
        return [(v.path, v.code) for v in audit_files(self.root, paths)]

    def test_named_public_document_is_accepted(self):
        self.put('README.md')
        self.assertEqual(self.codes(['README.md']), [])

    def test_exact_authorized_instrument_paths_are_accepted(self):
        for name in ['tools/b210_instrument.py', 'tests/public/test_b210_instrument.py',
                     'docs/public/B210_INSTRUMENT.md']:
            with self.subTest(name=name):
                self.put(name)
                self.assertEqual(self.codes([name]), [])
                self.put(name + '.raw.jsonl')
                self.assertEqual(self.codes([name + '.raw.jsonl']),
                                 [(name + '.raw.jsonl', 'not-allowlisted')])

    def test_withheld_research_and_runtime_paths_are_rejected(self):
        for name in ['docs/skripsi_research_ops/datasets/withheld/gold.json',
                     'docs/skripsi_research_ops/reviews/session/events.jsonl',
                     'kode_implementasi_dan_pengujian/test_remote_gemma.py',
                     'wildan-jetson-20261025/model.gguf', '.env', 'debug.log',
                     'docs/public/copied-gold.json']:
            with self.subTest(name=name):
                self.assertEqual(self.codes([name]), [(name, 'not-allowlisted')])

    def test_parent_absolute_and_noncanonical_paths_are_rejected(self):
        for name in ['../README.md', '/README.md', './README.md', 'docs//public/x',
                     'docs\\public\\x', 'README.md\x00', '']:
            with self.subTest(name=name):
                self.assertEqual(self.codes([name]), [(name, 'invalid-path')])

    def test_missing_allowlisted_file_fails(self):
        self.assertEqual(self.codes(['README.md']), [('README.md', 'missing-or-not-file')])

    def test_directory_cannot_masquerade_as_file(self):
        (self.root / 'README.md').mkdir()
        self.assertEqual(self.codes(['README.md']), [('README.md', 'missing-or-not-file')])

    def test_symlink_to_outside_is_not_read(self):
        (self.root / 'README.md').symlink_to(self.root.parent / 'not-readable')
        self.assertEqual(self.codes(['README.md']), [('README.md', 'symlink')])

    def test_parent_symlink_is_rejected(self):
        outside = self.root / 'elsewhere'
        outside.mkdir()
        (self.root / 'docs').symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.codes(['docs/public/GITHUB_WORKFLOW.md']),
                         [('docs/public/GITHUB_WORKFLOW.md', 'symlink')])

    def test_known_private_key_format_is_rejected_without_echo(self):
        marker = b'-----BEGIN ' + b'OPENSSH PRIVATE KEY-----'
        self.put('README.md', marker)
        self.assertEqual(self.codes(['README.md']), [('README.md', 'credential-pattern')])

    def test_known_github_credential_format_is_rejected(self):
        for prefix, suffix in [(b'ghp_', b'A' * 36), (b'github_pat_', b'B' * 50)]:
            with self.subTest(prefix=prefix):
                self.put('README.md', prefix + suffix)
                self.assertEqual(self.codes(['README.md']), [('README.md', 'credential-pattern')])

    def test_oversize_file_is_rejected(self):
        self.put('README.md', b'x' * (256 * 1024 + 1))
        self.assertEqual(self.codes(['README.md']), [('README.md', 'oversize')])

    def test_limit_sized_file_is_accepted(self):
        self.put('README.md', b'x' * (256 * 1024))
        self.assertEqual(self.codes(['README.md']), [])


class RealGitCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git('init', '--quiet')

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def put(self, name, text='public\n'):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def cli(self, root=None):
        return subprocess.run([sys.executable, str(CHECKER), '--root', str(root or self.root)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_cli_checks_index_and_ignores_untracked_private_material(self):
        self.put('README.md')
        self.put('gold.json', 'withheld fixture\n')
        self.git('add', 'README.md')
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('1 tracked files', result.stdout)
        self.assertNotIn('withheld fixture', result.stdout + result.stderr)

    def test_force_added_private_path_is_rejected(self):
        shutil.copyfile(SOURCE_ROOT / '.gitignore', self.root / '.gitignore')
        self.put('docs/skripsi_research_ops/datasets/fixture/gold.json')
        self.git('add', '-f', 'docs/skripsi_research_ops/datasets/fixture/gold.json')
        result = self.cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn('not-allowlisted', result.stderr)

    def test_real_gitignore_holds_private_paths_but_allows_public_docs(self):
        shutil.copyfile(SOURCE_ROOT / '.gitignore', self.root / '.gitignore')
        blocked = ['docs/skripsi_research_ops/datasets/fixture/gold.json',
                   'docs/skripsi_research_ops/sessions/private.md',
                   'kode_implementasi_dan_pengujian/startup.log',
                   'wildan-jetson-20261025/anything', '.env', 'archive.zip']
        for path in blocked:
            with self.subTest(path=path):
                self.assertEqual(self.git('check-ignore', '--no-index', path).returncode, 0)
        for path in ['README.md', 'tools/check_public_tree.py',
                     'docs/public/GITHUB_WORKFLOW.md', '.github/workflows/quality.yml']:
            with self.subTest(path=path):
                result = subprocess.run(['git', '-C', str(self.root), 'check-ignore',
                                         '--no-index', path], capture_output=True)
                self.assertEqual(result.returncode, 1, result.stderr)

    def test_cli_empty_index_is_not_success(self):
        self.assertEqual(self.cli().returncode, 2)

    def test_cli_non_git_directory_is_not_success(self):
        with tempfile.TemporaryDirectory() as other:
            self.assertEqual(self.cli(Path(other)).returncode, 2)

    def test_cli_missing_root_is_not_success(self):
        self.assertEqual(self.cli(self.root / 'absent').returncode, 2)

    def test_cli_rejects_tracked_symlink(self):
        (self.root / 'README.md').symlink_to('absent')
        self.git('add', 'README.md')
        result = self.cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn('symlink', result.stderr)

    def test_cli_rejects_secret_staged_before_worktree_was_sanitized(self):
        token = 'ghp_' + 'Q' * 36
        self.put('README.md', token)
        self.git('add', 'README.md')
        self.put('README.md', 'safe but not restaged\n')
        result = self.cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn('credential-pattern', result.stderr)
        self.assertNotIn(token, result.stdout + result.stderr)

    def test_cli_rejects_tracked_credential_without_exposing_value(self):
        token = 'ghp_' + 'Z' * 36
        self.put('README.md', token)
        self.git('add', 'README.md')
        result = self.cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn('credential-pattern', result.stderr)
        self.assertNotIn(token, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
