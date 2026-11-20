import json
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import b210_live as live
from tools.b210_instrument import digest, prepare


WORKER = '''import hashlib, json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
q = next(a.split('=', 1)[1] for a in args if a.startswith('--question='))
index = Path(args[args.index('--index') + 1])
path = Path(args[args.index('--log') + 1])
action = Path('action').read_text()
assert os.environ['HF_HUB_OFFLINE'] == '1'
assert '--log-retrieval-only' in args if '--retrieval-only' in args else '--log-retrieval-only' not in args
print('fixture stdout', flush=True)
print('fixture stderr', file=sys.stderr, flush=True)
if action == 'timeout':
    time.sleep(20)
if action == 'fail':
    sys.exit(7)
if action == 'missing':
    sys.exit(0)
if action == 'malformed':
    path.write_bytes(b'{')
    sys.exit(0)
if action == 'change':
    (index / 'chunks.jsonl').write_text('changed')
r = dict(schema_version='2.8-safety-overlay', question=q, index_dir=str(index),
         script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
         requested_top_k=1, requested_safety_candidate_k=5, retrieval_mode='hybrid',
         safety_mode='conservative', max_context_chars=1100, requested_coverage=True,
         requested_disease_filter=True, requested_clinical_priors=True,
         generation_params=dict(temperature=0, enable_thinking=False, max_tokens=128),
         retrieved_ids=[1], included_evidence=[])
if action == 'wrong_question':
    r['question'] = 'wrong'
if action == 'raw_error':
    r['status'] = 'error'
elif '--retrieval-only' in args:
    r['status'] = 'retrieval_only'
else:
    status, origin, model = {
        'A': ('success', 'model_output', True),
        'B': ('safety_abstained_pre_generation', 'safety_template', False),
        'C': ('safety_source_scoped_answer_pre_generation', 'source_text_extract', False),
        'D': ('safety_blocked_post_generation', 'safety_template', True),
        'E': ('safety_rewritten_source_extract', 'source_text_extract', True),
    }[q[-1]]
    r.update(status=status, displayed_answer_origin=origin, answer='fixture answer')
    if model:
        r['model'] = 'fixture-model'
path.write_bytes((json.dumps(r, separators=(', ', ': ')) + '\\n').encode())
if action == 'raw_error':
    sys.exit(9)
'''


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux worker fixtures')
class LiveAdapterTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.rag = self.root / 'rag'
        self.index = self.rag / 'index'
        self.index.mkdir(parents=True)
        self.files = tuple(self.index / x for x in ('manifest.json', 'chunks.jsonl', 'index.faiss'))
        for p in self.files:
            p.write_text('fixture')
        self.script = self.rag / 'ask_rag_v2_2_b29_v06fix.py'
        self.script.write_text(WORKER)
        self.action = self.rag / 'action'
        self.action.write_text('ok')
        cases, gold = (self.root / f'{name}.json' for name in ('cases', 'gold'))
        cases.write_text(json.dumps({'cases': [dict(id=c, label='fixture', question=f'--synthetic {c}')
                                               for c in 'ABCDE']}))
        gold.write_text(json.dumps({'items': [dict(id=c, stratum='fixture', hidden='GOLD_SENTINEL')
                                              for c in 'ABCDE']}))
        self.bundle = self.root / 'bundle'
        prepare(cases, gold, self.bundle, artifacts=(self.script, *self.files))
        self.receipt = live.Receipt('fixture-run', 'fixture-session', 'p', 'f', 'a',
                                    digest(self.bundle / 'manifest.json'), 'fixture-model', 5)
        self.output = self.root / 'output'

    def execute(self, review=None, **overrides):
        args = dict(bundle=self.bundle, rag_root=self.rag, index=self.index,
                    interpreter=Path(sys.executable), api_base='http://127.0.0.1:1',
                    output=self.output, receipt=self.receipt, review=review)
        return live.run(**(args | overrides))

    def test_order_status_provenance_and_respecting_review(self):
        visited = []

        def review(meta, raw):
            ledger = json.loads((self.output / 'attempts.json').read_text())
            self.assertEqual(ledger['attempts'][meta['ordinal'] - 1]['status'], 'review_required')
            self.assertEqual(meta['raw_sha256'], digest(raw))
            visited.append(meta['case_id'])
            return live.Review('fixture-reviewer', 'CONTINUE', 'fixture disposition')

        report = self.execute(review)
        expected = json.loads((self.bundle / 'order.json').read_text())['ids']
        rows = report['attempts']
        self.assertEqual([(r['stage'], r['case_id']) for r in rows],
                         [(s, c) for s in ('retrieval', 'generation') for c in expected])
        self.assertEqual(visited, expected)
        self.assertEqual(report['state'], 'completed_execution_not_quality_pass')
        self.assertIsNone(report['next_ordinal'])
        self.assertEqual(report['receipt']['runtime_session'], 'fixture-session')
        self.assertEqual({r['case_id']: r['classification']['outcome'] for r in rows[5:]},
                         dict(A='model_answer', B='abstain', C='limited', D='abstain', E='rewrite'))
        for r in rows:
            self.assertEqual((r['status'], r['quality'], r['attempt_type']),
                             ('executed', 'NOT_ASSESSED', 'primary'))
            self.assertNotIn('GOLD_SENTINEL', ' '.join(r['command']))
            self.assertIn('--question=--synthetic ' + r['case_id'], r['command'])
            self.assertIn('fixture stderr', Path(r['stdout_stderr']).read_text())
            self.assertEqual(r['raw_sha256'], digest(Path(r['raw'])))
            self.assertIn(b'"schema_version": "2.8-safety-overlay"', Path(r['raw']).read_bytes())
        self.assertEqual(json.loads((self.output / 'attempts.json').read_text()), report)

    def test_missing_review_pauses_and_same_output_cannot_replace_primary(self):
        report = self.execute()
        before = (self.output / 'attempts.json').read_bytes()
        self.assertEqual((report['state'], report['stop_ordinal'], report['next_ordinal']), ('paused', 6, 6))
        self.assertEqual(report['attempts'][5]['status'], 'review_required')
        self.assertTrue(all(r['status'] == 'NOT_RUN' for r in report['attempts'][6:]))
        with self.assertRaises(FileExistsError):
            self.execute()
        self.assertEqual((self.output / 'attempts.json').read_bytes(), before)

    def test_unresolved_and_boolean_review_do_not_continue(self):
        for decision, expected in ((live.Review('fixture', 'UNRESOLVED', 'suspected'), 'critical_unresolved'),
                                   (True, 'review_error')):
            with self.subTest(expected=expected):
                report = self.execute(lambda *_: decision, output=self.root / expected)
                self.assertEqual(report['attempts'][5]['status'], expected)
                self.assertEqual(report['attempts'][6]['status'], 'NOT_RUN')

    def test_changed_raw_after_human_review_pauses(self):
        def change(_, raw):
            raw.write_bytes(b'changed')
            return live.Review('fixture', 'CONTINUE', 'fixture decision')
        report = self.execute(change)
        self.assertEqual(report['attempts'][5]['status'], 'artifact_changed')
        self.assertEqual(report['attempts'][6]['status'], 'NOT_RUN')

    def test_worker_failure_and_invalid_raw_stop_before_next_case(self):
        for action, expected, code in [('fail', 'failed', 7), ('missing', 'invalid_or_missing_log', 0),
                                       ('malformed', 'invalid_or_missing_log', 0),
                                       ('wrong_question', 'invalid_or_missing_log', 0),
                                       ('raw_error', 'failed', 9), ('change', 'artifact_changed', 0)]:
            with self.subTest(action=action):
                self.action.write_text(action)
                report = self.execute(output=self.root / action)
                row = report['attempts'][0]
                self.assertEqual((row['status'], row['exit_code']), (expected, code))
                self.assertEqual(report['attempts'][1]['status'], 'NOT_RUN')
                if action == 'raw_error':
                    self.assertEqual(row['raw_status'], 'error')

    def test_worker_timeout_keeps_terminal_output(self):
        self.action.write_text('timeout')
        report = self.execute(receipt=replace(self.receipt, timeout_seconds=0.75))
        row = report['attempts'][0]
        self.assertEqual(row['status'], 'timeout')
        self.assertIn('fixture stdout', Path(row['stdout_stderr']).read_text())
        self.assertEqual(report['attempts'][1]['status'], 'NOT_RUN')

    def test_interrupt_persists_stop_and_never_tries_following_case(self):
        with patch.object(live, '_worker', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.execute()
        report = json.loads((self.output / 'attempts.json').read_text())
        self.assertEqual(report['attempts'][0]['status'], 'interrupted')
        self.assertEqual(report['attempts'][1]['status'], 'NOT_RUN')
        self.assertEqual(report['stop_ordinal'], 1)

    def test_wrong_model_pauses_even_on_exit_zero(self):
        report = self.execute(receipt=replace(self.receipt, model_id='wrong-model'),
                              review=lambda *_: live.Review('fixture', 'CONTINUE', 'fixture'))
        row = next(r for r in report['attempts'] if r['status'] == 'invalid_or_missing_log')
        self.assertEqual(row['raw_error'], 'API model mismatch')

    def test_prelaunch_rejections_leave_no_output(self):
        for changes in (dict(receipt=replace(self.receipt, timeout_seconds=float('nan'))),
                        dict(receipt=replace(self.receipt, preflight_ref='')),
                        dict(receipt=replace(self.receipt, draft_manifest_sha256='0' * 64)),
                        dict(api_base='http://example.invalid:1'), dict(index=self.rag),
                        dict(output=self.index / 'output')):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.execute(**changes)
            self.assertFalse(changes.get('output', self.output).exists())
