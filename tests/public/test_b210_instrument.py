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
if mode=='deep': Path(raw).write_text('{"nested":'+ '['*2000+'0'+']'*2000+'}'); sys.exit(0)
if mode.startswith('status:'): Path(raw).write_text(json.dumps({'status':mode[7:]})); sys.exit(0)
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
        """Oracle: literal stdlib shuffle of SYN00..07 with seed20261119.

        Established independently of prepare/verify; input-order preservation or
        another seed must fail even if both project functions share that error.
        """
        original = (self.cases.read_bytes(), self.gold.read_bytes())
        a, b = self.root / 'a', self.root / 'b'
        m = prepare(self.cases, self.gold, a, artifacts=(self.worker,))
        prepare(self.cases, self.gold, b)
        self.assertEqual((a/'cases.json').read_bytes(), original[0])
        self.assertEqual((a/'gold.json').read_bytes(), original[1])
        self.assertEqual((a/'order.json').read_bytes(), (b/'order.json').read_bytes())
        order = json.loads((a/'order.json').read_text())['ids']
        self.assertEqual(order, ['SYN02', 'SYN03', 'SYN00', 'SYN01',
                                 'SYN06', 'SYN04', 'SYN05', 'SYN07'])
        self.assertCountEqual(order, [row['id'] for row in self.rows])
        self.assertEqual(m['seed'], 20261119)
        self.assertEqual(m['order_algorithm'], 'python.random.Random.shuffle')
        self.assertEqual(m['schema_version'], 1)
        self.assertEqual(m['python_version'], sys.version.split()[0])
        self.assertEqual(m['planned_primary_attempts'], 16)
        self.assertFalse(m['freeze_approved'])
        self.assertFalse(m['execution_authorized'])
        self.assertEqual(verify_bundle(a)['files'], m['files'])
        self.assertEqual((self.cases.read_bytes(), self.gold.read_bytes()), original)

    def test_seed_override_is_not_an_api_or_cli_option(self):
        out = self.root/'out'
        with self.assertRaises(TypeError):
            prepare(self.cases, self.gold, out, seed=20261120)
        result = subprocess.run(
            [sys.executable, str(CLI), 'prepare', '--cases', str(self.cases),
             '--gold', str(self.gold), '--output', str(out), '--seed', '20261120'],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(out.exists())

    def test_malformed_and_deep_json_rejected_at_all_input_boundaries(self):
        payloads = [b'{"fixture_secret":',
                    b'{"fixture_secret":' + b'['*2000 + b'0' + b']'*2000 + b'}']
        for index, payload in enumerate(payloads):
            for path in (self.cases, self.gold):
                with self.subTest(index=index, input=path.name):
                    original = path.read_bytes()
                    path.write_bytes(payload)
                    out = self.root/'invalid'
                    with self.assertRaises(ValueError):
                        prepare(self.cases, self.gold, out)
                    result = subprocess.run(
                        [sys.executable, str(CLI), 'prepare', '--cases', str(self.cases),
                         '--gold', str(self.gold), '--output', str(out)],
                        capture_output=True, text=True)
                    self.assertEqual(result.returncode, 2)
                    self.assertNotIn('fixture_secret', result.stdout + result.stderr)
                    self.assertNotIn('Traceback', result.stdout + result.stderr)
                    self.assertFalse(out.exists())
                    path.write_bytes(original)
            for name in ('manifest.json', 'cases.json', 'gold.json', 'order.json'):
                with self.subTest(index=index, snapshot=name):
                    out = self.root/f'bundle-{index}-{name}'
                    prepare(self.cases, self.gold, out)
                    (out/name).write_bytes(payload)
                    if name != 'manifest.json':
                        manifest = json.loads((out/'manifest.json').read_text())
                        manifest['files'][name] = digest(out/name)
                        (out/'manifest.json').write_text(json.dumps(manifest))
                    with self.assertRaises(ValueError):
                        verify_bundle(out)
                    result = subprocess.run([sys.executable, str(CLI), 'verify', str(out)],
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 2)
                    self.assertNotIn('fixture_secret', result.stdout + result.stderr)
                    self.assertNotIn('Traceback', result.stdout + result.stderr)

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
                          ('seed',20261120), ('order_algorithm','input_order'),
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
        for mode in ['missing','bad','deep','multi','error']:
            with self.subTest(mode=mode):
                r = supervise_tasks([self.task(mode),self.task(case='SYN01')], self.root/mode, 5)
                self.assertEqual([a['status'] for a in r['attempts']],
                                 ['invalid_or_missing_log','NOT_RUN'])

    def test_only_prototype_stage_statuses_are_accepted(self):
        statuses = ['retrieval_only', 'success', 'safety_abstained_pre_generation',
                    'safety_source_scoped_answer_pre_generation',
                    'safety_blocked_post_generation', 'safety_rewritten_source_extract']
        for stage, accepted in [('retrieval', 'retrieval_only'), ('generation', 'success')]:
            for status in statuses:
                with self.subTest(stage=stage, status=status):
                    r = supervise_tasks(
                        [self.task('status:'+status, stage=stage),
                         self.task('status:'+accepted, case='SYN01', stage=stage)],
                        self.root/f'{stage}-{status}', 5, review=lambda _: True)
                    expected = (['executed', 'executed'] if status == accepted else
                                ['invalid_or_missing_log', 'NOT_RUN'])
                    self.assertEqual([a['status'] for a in r['attempts']], expected)

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
            self.supervise([self.task('success',stage='generation'),
                            self.task('success',case='SYN01',stage='generation')], review=review)
        r = json.loads((self.root/'attempts/attempts.json').read_text())
        self.assertEqual([a['status'] for a in r['attempts']], ['interrupted', 'NOT_RUN'])
        self.assertEqual(r['state'], 'paused')
        self.assertTrue(r['attempts'][0]['worker_launched'])

    def test_interrupt_across_attempt_lifecycle_persists_and_reraises(self):
        from tools import b210_instrument as instrument
        for boundary in ('pre_hash', 'raw_read', 'post_hash', 'ledger_write'):
            with self.subTest(boundary=boundary):
                calls = 0
                target = {'pre_hash': 'digest', 'post_hash': 'digest',
                          'raw_read': '_read_result', 'ledger_write': '_new_file'}[boundary]
                real = getattr(instrument, target)
                def interrupt(*args, **kwargs):
                    nonlocal calls
                    calls += 1
                    result = real(*args, **kwargs)
                    trigger = 2 if boundary in ('post_hash', 'ledger_write') else 1
                    if calls == trigger:
                        raise KeyboardInterrupt
                    return result
                out = self.root/boundary
                fingerprint = digest(self.worker)
                with patch.object(instrument, target, interrupt):
                    with patch.object(instrument, '_terminate', wraps=instrument._terminate) as stop:
                        with self.assertRaises(KeyboardInterrupt):
                            supervise_tasks([self.task(), self.task(case='SYN01')], out, 5,
                                            expected_hashes={str(self.worker): fingerprint})
                report = json.loads((out/'attempts.json').read_text())
                self.assertEqual(report['state'], 'paused')
                self.assertEqual([a['status'] for a in report['attempts']],
                                 ['interrupted', 'NOT_RUN'])
                launched = boundary in ('raw_read', 'post_hash')
                row = report['attempts'][0]
                self.assertEqual(row['worker_launched'], launched)
                self.assertEqual(stop.call_count, int(launched))
                self.assertEqual(row['exit_code'], 0 if launched else None)
                self.assertTrue(row['ended_at'])
                if launched:
                    self.assertIn('synthetic worker started', Path(row['stdout']).read_text())
                self.assertFalse((out/'retrieval--SYN01').exists())
                self.assertFalse((out/'attempts.tmp').exists())

    def test_artifact_change_during_review_is_detected(self):
        fingerprint = digest(self.worker)
        def review(_):
            self.worker.write_text('changed fixture')
            return True
        r = self.supervise([self.task('success', stage='generation'),
                            self.task('success', case='SYN01', stage='generation')],
                           expected_hashes={str(self.worker): fingerprint}, review=review)
        self.assertEqual([a['status'] for a in r['attempts']], ['artifact_changed', 'NOT_RUN'])
        self.assertEqual(r['attempts'][0]['prior_status'], 'executed')
        self.assertEqual(r['state'], 'paused')

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
