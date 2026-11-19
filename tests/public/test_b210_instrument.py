"""Offline behavioral fixtures. No withheld cases, RAG imports, models or APIs."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.b210_instrument import Task, digest, prepare, supervise_tasks, verify_bundle

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / 'tools/b210_instrument.py'
# This fake worker only prints/writes synthetic local data or sleeps.
WORKER = '''import json,sys,time
from pathlib import Path
mode,raw=sys.argv[1:]
print('synthetic worker started',flush=True)
if mode=='timeout': time.sleep(30)
if mode=='fail': sys.exit(7)
if mode=='missing': sys.exit(0)
if mode=='bad': Path(raw).write_text('not JSON'); sys.exit(0)
if mode=='multi': Path(raw).write_text('{}\\n{}\\n'); sys.exit(0)
if mode=='error': Path(raw).write_text(json.dumps({'status':'error'})); sys.exit(0)
row={'status':'retrieval_only' if mode=='retrieval' else 'success'}
if mode=='critical': row['critical_suspected']=True
Path(raw).write_text(json.dumps(row)+'\\n')
'''


class FixtureBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cases = self.root / 'input-cases.json'
        self.gold = self.root / 'input-gold.json'
        self.rows = [{'id': f'SYN{i:02}', 'question': f'fixture question {i}',
                      'label': 'fixture'} for i in range(8)]
        self.cases.write_text(json.dumps({'cases': self.rows}))
        self.gold.write_text(json.dumps({'items': [
            {'id': x['id'], 'stratum': x['label']} for x in self.rows]}))
        self.worker = self.root / 'worker.py'
        self.worker.write_text(WORKER)

    def task(self, mode='retrieval', case='SYN00', stage='retrieval'):
        return Task(case, stage, (sys.executable, str(self.worker), mode, '{raw_log}'))

    def supervise(self, tasks=None, **kwargs):
        return supervise_tasks(tasks or [self.task()], self.root / 'attempts',
                               kwargs.pop('timeout', 5), **kwargs)


class PreparationTests(FixtureBase):
    def test_draft_exact_snapshots_seed_and_both_stage_counts(self):
        original = (self.cases.read_bytes(), self.gold.read_bytes())
        a, b = self.root / 'a', self.root / 'b'
        m = prepare(self.cases, self.gold, a, artifacts=(self.worker,))
        prepare(self.cases, self.gold, b)
        self.assertEqual((a/'cases.json').read_bytes(), original[0])
        self.assertEqual((a/'gold.json').read_bytes(), original[1])
        self.assertEqual((a/'order.json').read_bytes(), (b/'order.json').read_bytes())
        self.assertEqual(len(set(json.loads((a/'order.json').read_text())['ids'])), 8)
        self.assertEqual(m['planned_primary_attempts'], 16)
        self.assertFalse(m['freeze_approved'])
        self.assertFalse(m['execution_authorized'])
        self.assertEqual(verify_bundle(a)['files'], m['files'])
        self.assertEqual((self.cases.read_bytes(), self.gold.read_bytes()), original)

    def test_duplicate_case_id_fails_before_output_creation(self):
        self.rows[1]['id'] = self.rows[0]['id']
        self.cases.write_text(json.dumps({'cases': self.rows}))
        with self.assertRaises(ValueError):
            prepare(self.cases, self.gold, self.root/'out')
        self.assertFalse((self.root/'out').exists())

    def test_unsafe_id_mismatch_missing_and_duplicate_gold_rejected(self):
        original = self.gold.read_bytes()
        for items in [[{'id':'../escape', 'stratum':'fixture'}], [],
                      [{'id':'SYN00','stratum':'wrong'}],
                      [{'id':'SYN00','stratum':'fixture'}]*2]:
            with self.subTest(items=items):
                self.gold.write_text(json.dumps({'items': items}))
                with self.assertRaises(ValueError):
                    prepare(self.cases, self.gold, self.root/'out')
                self.assertFalse((self.root/'out').exists())
        self.gold.write_bytes(original)
        self.rows[0]['id'] = '../escape'
        self.cases.write_text(json.dumps({'cases': self.rows}))
        with self.assertRaises(ValueError):
            prepare(self.cases, self.gold, self.root/'out')

    def test_empty_question_invalid_root_and_empty_cases_rejected(self):
        for obj in [[], {'cases': []}, {'cases': [{'id':'x','label':'fixture','question':''}]}]:
            self.cases.write_text(json.dumps(obj))
            with self.assertRaises(ValueError):
                prepare(self.cases, self.gold, self.root/'out')

    def test_output_overwrite_refused(self):
        out = self.root/'out'
        prepare(self.cases, self.gold, out)
        before = digest(out/'manifest.json')
        with self.assertRaises(FileExistsError):
            prepare(self.cases, self.gold, out)
        self.assertEqual(digest(out/'manifest.json'), before)

    def test_snapshot_tampering_and_artifact_change_fail(self):
        out = self.root/'out'
        prepare(self.cases, self.gold, out, artifacts=(self.worker,))
        self.worker.write_text('changed fixture')
        with self.assertRaises(ValueError):
            verify_bundle(out)
        (out/'cases.json').write_text('{}')
        with self.assertRaises(ValueError):
            verify_bundle(out)

    def test_manifest_tampering_not_mistaken_for_frozen_permission(self):
        out = self.root/'out'
        prepare(self.cases, self.gold, out)
        path = out/'manifest.json'
        original = path.read_bytes()
        for key,value in [('execution_authorized',True), ('freeze_approved',True),
                          ('state','FROZEN'), ('case_count',999), ('seed',False),
                          ('stages',['generation','retrieval'])]:
            m = json.loads(original);m[key] = value;path.write_text(json.dumps(m))
            with self.assertRaises(ValueError):
                verify_bundle(out)
        path.write_bytes(original)

    def test_snapshot_symlink_rejected(self):
        out = self.root/'out'
        prepare(self.cases, self.gold, out)
        (out/'cases.json').unlink();(out/'cases.json').symlink_to(self.cases)
        with self.assertRaises(ValueError):
            verify_bundle(out)

    def test_cli_prepare_and_verify_do_not_spawn_or_authorize_runtime(self):
        out = self.root/'out'
        cmd = [sys.executable, str(CLI)]
        result = subprocess.run(cmd+['prepare','--cases',str(self.cases), '--gold',str(self.gold),
                                     '--output',str(out)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('DRAFT NOT FROZEN, NOT RUN', result.stdout)
        self.assertNotIn('fixture question', result.stdout+result.stderr)
        result = subprocess.run(cmd+['verify',str(out)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run(cmd+['execute',str(out)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)


@unittest.skipUnless(os.name == 'posix', 'POSIX supervisor')
class SupervisionTests(FixtureBase):
    def test_success_records_exit_and_streamed_stdout_not_quality_pass(self):
        r = self.supervise()
        self.assertEqual(r['state'], 'completed_execution_not_quality_pass')
        a = r['attempts'][0]
        self.assertEqual(a['status'], 'executed');self.assertEqual(a['exit_code'], 0)
        self.assertIn('synthetic worker started', Path(a['stdout']).read_text())
        self.assertTrue(a['started_at']);self.assertTrue(a['ended_at'])
        self.assertEqual(json.loads((self.root/'attempts/attempts.json').read_text()), r)

    def test_failure_pauses_next_not_run_and_never_retries(self):
        r = self.supervise([self.task('fail'),self.task(case='SYN01')])
        self.assertEqual([a['status'] for a in r['attempts']], ['failed','NOT_RUN'])
        self.assertEqual(r['attempts'][0]['exit_code'], 7)
        self.assertFalse((self.root/'attempts/retrieval--SYN01').exists())
        self.assertEqual(len(list((self.root/'attempts').glob('retrieval--*'))), 1)

    def test_timeout_preserves_partial_stdout_and_pauses(self):
        r = self.supervise([self.task('timeout'),self.task(case='SYN01')], timeout=0.5)
        self.assertEqual([a['status'] for a in r['attempts']], ['timeout','NOT_RUN'])
        self.assertIn('synthetic worker started',Path(r['attempts'][0]['stdout']).read_text())

    def test_missing_invalid_error_and_multirecord_logs_pause(self):
        for mode in ['missing','bad','multi','error']:
            with self.subTest(mode=mode):
                r = supervise_tasks([self.task(mode),self.task(case='SYN01')], self.root/mode, 5)
                self.assertEqual([a['status'] for a in r['attempts']],
                                 ['invalid_or_missing_log','NOT_RUN'])

    def test_launch_failure_is_attempted_not_silently_missing(self):
        task = Task('SYN00','retrieval',('/nonexistent/synthetic-worker','{raw_log}'))
        r = self.supervise([task,self.task(case='SYN01')])
        self.assertEqual([a['status'] for a in r['attempts']], ['launch_error','NOT_RUN'])
        self.assertIsNone(r['attempts'][0]['exit_code'])

    def test_critical_indication_pauses_even_with_approving_callback(self):
        r = self.supervise([self.task('critical',stage='generation'),
                            self.task('success',case='SYN01',stage='generation')], review=lambda _: True)
        self.assertEqual([a['status'] for a in r['attempts']], ['critical_suspected','NOT_RUN'])

    def test_generation_requires_explicit_review_callback(self):
        r = self.supervise([self.task('success',stage='generation')])
        self.assertEqual(r['attempts'][0]['status'], 'review_required')
        r = supervise_tasks([self.task('success',stage='generation')], self.root/'reviewed', 5,
                            review=lambda _: True)
        self.assertEqual(r['attempts'][0]['status'], 'executed')

    def test_review_exception_fails_closed(self):
        for exception in (RuntimeError, ValueError, OSError):
            with self.subTest(exception=exception):
                def review(_):
                    raise exception('synthetic fixture')
                r = supervise_tasks([self.task('success',stage='generation')],
                                    self.root/exception.__name__, 5, review=review)
                self.assertEqual(r['attempts'][0]['status'], 'review_error')
                self.assertEqual(r['attempts'][0]['error_type'], exception.__name__)

    def test_interrupt_during_review_is_recorded_not_left_starting(self):
        def review(_):
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.supervise([self.task('success',stage='generation')], review=review)
        r = json.loads((self.root/'attempts/attempts.json').read_text())
        self.assertEqual(r['attempts'][0]['status'], 'interrupted')

    def test_artifact_change_during_worker_is_detected_after_exit(self):
        watched = self.root/'watched.txt';watched.write_text('original')
        code = ('import sys,json;from pathlib import Path;'
                'Path(sys.argv[2]).write_text("changed");'
                'Path(sys.argv[1]).write_text(json.dumps({"status":"retrieval_only"}))')
        task = Task('SYN00','retrieval',(sys.executable,'-c',code,'{raw_log}',str(watched)))
        r = self.supervise([task,self.task(case='SYN01')],
                           expected_hashes={str(watched):digest(watched)})
        self.assertEqual(r['attempts'][0]['artifact_check'], 'mismatch')
        self.assertEqual(r['attempts'][1]['status'], 'NOT_RUN')
        self.assertEqual(r['state'], 'paused')

    def test_artifact_change_pauses_before_starting_next_process(self):
        r = self.supervise(expected_hashes={str(self.worker):'0'*64})
        self.assertEqual(r['state'], 'paused_artifact_mismatch')
        self.assertEqual(r['attempts'][0]['status'], 'NOT_RUN')
        self.assertFalse((self.root/'attempts/retrieval--SYN00').exists())

    def test_unsafe_duplicate_out_of_order_and_bad_timeout_rejected(self):
        for tasks in [[self.task(case='../escape')], [self.task(),self.task()],
                      [self.task('success',stage='generation'),self.task()],
                      [Task('SYN00','retrieval',(sys.executable,'worker.py'))]]:
            with self.assertRaises(ValueError):
                self.supervise(tasks)
        for timeout in [0,-1,float('nan'),float('inf'),True]:
            with self.assertRaises(ValueError):
                self.supervise(timeout=timeout)
        self.assertFalse((self.root/'attempts').exists())

    def test_attempt_directory_never_overwritten(self):
        self.supervise()
        before = digest(self.root/'attempts/attempts.json')
        with self.assertRaises(FileExistsError):
            self.supervise()
        self.assertEqual(digest(self.root/'attempts/attempts.json'),before)

    def test_keyboard_interrupt_preserves_ledger_and_stops_own_worker(self):
        real_wait = subprocess.Popen.wait
        fired = False
        def wait(proc, *args, **kwargs):
            nonlocal fired
            if not fired:
                fired = True
                raise KeyboardInterrupt
            return real_wait(proc,*args,**kwargs)
        with patch.object(subprocess.Popen, 'wait', wait):
            with self.assertRaises(KeyboardInterrupt):
                self.supervise([self.task('timeout'),self.task(case='SYN01')])
        r = json.loads((self.root/'attempts/attempts.json').read_text())
        self.assertEqual([a['status'] for a in r['attempts']], ['interrupted','NOT_RUN'])


if __name__ == '__main__':
    unittest.main()
