#!/usr/bin/env python3
"""Local preparation and subprocess supervision; not a medical evaluator.

The CLI only prepares/verifies a DRAFT bundle. It cannot freeze or execute it.
The supervision API is exercised with synthetic workers; a live-run adapter,
preflight, freeze and explicit runtime permission remain separate work.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import re
import signal
import subprocess
import time
from typing import Callable

ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z')
STAGES = ('retrieval', 'generation')
VALID_STATUSES = {
    'retrieval_only', 'success', 'safety_abstained_pre_generation',
    'safety_source_scoped_answer_pre_generation', 'safety_blocked_post_generation',
    'safety_rewritten_source_extract',
}
MAX_JSON = 4 * 1024 * 1024


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + '\n').encode('utf-8')


def _new_file(path: Path, data: bytes):
    with path.open('xb') as stream:
        os.chmod(path, 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _object(data: bytes) -> dict:
    if len(data) > MAX_JSON:
        raise ValueError('JSON exceeds preparation size limit')
    obj = json.loads(data)
    if not isinstance(obj, dict):
        raise ValueError('JSON root must be an object')
    return obj


def _validate(cases: dict, gold: dict) -> list[str]:
    rows, items = cases.get('cases'), gold.get('items')
    if not isinstance(rows, list) or not rows or not isinstance(items, list):
        raise ValueError('nonempty cases and gold items lists required')
    ids, labels = [], {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('invalid case object')
        key = row.get('id')
        if not isinstance(key, str) or not ID.fullmatch(key) or key in labels:
            raise ValueError('invalid or duplicate case ID')
        for field in ('question', 'label'):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError('nonempty case question and label required')
        ids.append(key)
        labels[key] = row['label']
    seen = set()
    for row in items:
        if not isinstance(row, dict):
            raise ValueError('invalid gold object')
        key = row.get('id')
        if not isinstance(key, str) or key not in labels or key in seen:
            raise ValueError('gold IDs must match cases exactly once')
        if row.get('stratum') != labels[key]:
            raise ValueError('case label and gold stratum differ')
        seen.add(key)
    if seen != set(ids):
        raise ValueError('missing gold IDs')
    return ids


def prepare(cases_path: Path, gold_path: Path, output: Path, seed: int = 20261119,
            artifacts: tuple[Path, ...] = ()) -> dict:
    """Snapshot unchanged local inputs and a reproducible order; no model imports."""
    cases_data, gold_data = cases_path.read_bytes(), gold_path.read_bytes()
    ids = _validate(_object(cases_data), _object(gold_data))
    if type(seed) is not int:
        raise ValueError('seed must be an integer')
    order = list(ids)
    random.Random(seed).shuffle(order)
    artifact_rows = [{'path': str(p.resolve()), 'sha256': digest(p)} for p in artifacts]
    manifest = {
        'schema_version': 1, 'state': 'DRAFT_NOT_FROZEN',
        'freeze_approved': False, 'execution_authorized': False,
        'runtime_preflight': 'NOT_PERFORMED', 'seed': seed,
        'order_algorithm': 'python.random.Random.shuffle',
        'python_version': platform.python_version(),
        'created_at': datetime.now(timezone.utc).isoformat(),
        'case_count': len(ids), 'planned_primary_attempts': len(ids) * 2,
        'stages': list(STAGES), 'artifacts': artifact_rows,
        'policy': {'pause_on_failure': True, 'automatic_retry': False,
                   'automatic_restart': False, 'critical_detection': 'NOT_AUTOMATED',
                   'whole_process_timeout_seconds': None},
    }
    # Validate/read all inputs before creating the exclusive output directory.
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    snapshots = {'cases.json': cases_data, 'gold.json': gold_data,
                 'order.json': _json_bytes({'ids': order})}
    manifest['files'] = {}
    for name, data in snapshots.items():
        _new_file(output / name, data)
        manifest['files'][name] = hashlib.sha256(data).hexdigest()
    _new_file(output / 'manifest.json', _json_bytes(manifest))
    return manifest


def verify_bundle(root: Path) -> dict:
    """Verify draft integrity, not scientific eligibility or runtime permission."""
    manifest_path = root / 'manifest.json'
    if manifest_path.is_symlink():
        raise ValueError('manifest symlink rejected')
    m = _object(manifest_path.read_bytes())
    if (m.get('schema_version') != 1 or m.get('state') != 'DRAFT_NOT_FROZEN'
            or m.get('freeze_approved') is not False
            or m.get('execution_authorized') is not False):
        raise ValueError('only unapproved draft bundles are supported')
    files = m.get('files')
    if not isinstance(files, dict) or set(files) != {'cases.json', 'gold.json', 'order.json'}:
        raise ValueError('invalid snapshot file set')
    for name, expected in files.items():
        p = root / name
        if p.is_symlink() or not p.is_file() or digest(p) != expected:
            raise ValueError('snapshot integrity mismatch')
    ids = _validate(_object((root / 'cases.json').read_bytes()),
                    _object((root / 'gold.json').read_bytes()))
    order = _object((root / 'order.json').read_bytes()).get('ids')
    if not isinstance(order, list) or any(not isinstance(x, str) for x in order):
        raise ValueError('invalid order')
    expected_order = list(ids)
    if type(m.get('seed')) is not int:
        raise ValueError('invalid seed')
    random.Random(m['seed']).shuffle(expected_order)
    if (order != expected_order or m.get('order_algorithm') != 'python.random.Random.shuffle'
            or m.get('case_count') != len(ids) or m.get('stages') != list(STAGES)
            or m.get('planned_primary_attempts') != 2 * len(ids)):
        raise ValueError('order or plan metadata mismatch')
    for artifact in m.get('artifacts', []):
        if digest(Path(artifact['path'])) != artifact['sha256']:
            raise ValueError('artifact integrity mismatch')
    return m


@dataclass(frozen=True)
class Task:
    case_id: str
    stage: str
    argv: tuple[str, ...]  # Use the exact token {raw_log} for the output JSONL path.


def _save(path: Path, value):
    temporary = path.with_suffix('.tmp')
    _new_file(temporary, _json_bytes(value))
    os.replace(temporary, path)


def _terminate(proc: subprocess.Popen):
    """Stop this worker's own POSIX session, never a shared server or other PID."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait(timeout=5)


def _read_result(path: Path, stage: str) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError('missing or unsafe raw log')
    if path.stat().st_size > MAX_JSON:
        raise ValueError('raw log exceeds size limit')
    lines = [x for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]
    if len(lines) != 1:
        raise ValueError('exactly one raw record required per attempt')
    record = _object(lines[0].encode('utf-8'))
    status = record.get('status')
    allowed = {'retrieval_only'} if stage == 'retrieval' else VALID_STATUSES - {'retrieval_only'}
    if not isinstance(status, str) or status not in allowed:
        raise ValueError('raw status not valid for this stage')
    if 'critical_suspected' in record and type(record['critical_suspected']) is not bool:
        raise ValueError('invalid critical indication')
    return record


def supervise_tasks(tasks: list[Task], output: Path, timeout: float, *,
                    expected_hashes: dict[str, str] | None = None,
                    review: Callable[[dict], bool] | None = None) -> dict:
    """Trusted-command API, NOT an offline sandbox or authorization mechanism.

    Tests supply synthetic workers. A generation record needs a review callback
    returning True to continue; without one it pauses for human review. Neither
    status=success nor callback approval is a clinical safety determination.
    There is no resume/retry/production CLI; preserve paused ledgers and use a
    separately authorized, versioned integration for a later real experiment.
    """
    if os.name != 'posix':
        raise ValueError('POSIX process supervision required')
    if not isinstance(timeout, (float, int)) or isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('explicit finite positive process timeout required')
    if not tasks:
        raise ValueError('tasks required')
    seen, generation_seen = set(), False
    for task in tasks:
        if not ID.fullmatch(task.case_id) or task.stage not in STAGES:
            raise ValueError('invalid task identity')
        identity = (task.case_id, task.stage)
        if identity in seen or (generation_seen and task.stage == 'retrieval'):
            raise ValueError('duplicate task or invalid stage ordering')
        seen.add(identity)
        generation_seen |= task.stage == 'generation'
        if not task.argv or any(not isinstance(a, str) or not a for a in task.argv):
            raise ValueError('nonempty argument vector required')
        if task.argv.count('{raw_log}') != 1:
            raise ValueError('one raw_log placeholder required')
    expected_hashes = dict(expected_hashes or {})
    for name, expected in expected_hashes.items():
        if not isinstance(name, str) or not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise ValueError('artifact paths and SHA256 fingerprints required')
    output.mkdir(mode=0o700, exist_ok=False)
    report = {'schema_version': 1, 'purpose': 'supervision_not_quality_scoring',
              'state': 'running', 'automatic_retry': False,
              'artifact_expectations': expected_hashes,
              'attempts': [{'case_id': t.case_id, 'stage': t.stage,
                            'attempt_type': 'primary', 'status': 'NOT_RUN'} for t in tasks]}
    ledger = output / 'attempts.json'
    _save(ledger, report)
    for task, row in zip(tasks, report['attempts']):
        try:
            for name, expected in (expected_hashes or {}).items():
                if digest(Path(name)) != expected:
                    raise ValueError('artifact changed')
        except (OSError, ValueError):
            report['state'] = 'paused_artifact_mismatch'
            _save(ledger, report)
            return report
        folder = output / f'{task.stage}--{task.case_id}'
        folder.mkdir(mode=0o700)
        raw, terminal = folder / 'raw.jsonl', folder / 'stdout.txt'
        argv = [str(raw.resolve()) if a == '{raw_log}' else a for a in task.argv]
        row.update(status='STARTING', command=argv, stdout=str(terminal.resolve()),
                   raw=str(raw.resolve()), started_at=datetime.now(timezone.utc).isoformat(),
                   timeout_seconds=timeout, exit_code=None)
        _save(ledger, report)
        start, proc, interrupted = time.monotonic(), None, False
        with terminal.open('xb') as stream:
            os.chmod(terminal, 0o600)
            try:
                proc = subprocess.Popen(argv, stdout=stream, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, start_new_session=True)
                row['exit_code'] = proc.wait(timeout=timeout)
                row['status'] = 'executed' if proc.returncode == 0 else 'failed'
            except subprocess.TimeoutExpired:
                _terminate(proc)
                row.update(status='timeout', exit_code=proc.returncode)
            except KeyboardInterrupt:
                if proc is not None:
                    _terminate(proc)
                row.update(status='interrupted', exit_code=proc.returncode if proc else None)
                interrupted = True
            except OSError as exc:
                row.update(status='launch_error', error_type=type(exc).__name__)
            finally:
                stream.flush()
                os.fsync(stream.fileno())
        row.update(ended_at=datetime.now(timezone.utc).isoformat(),
                   duration_seconds=time.monotonic() - start)
        if row['status'] == 'executed':
            try:
                record = _read_result(raw, task.stage)
            except (ValueError, OSError, UnicodeError):
                row['status'] = 'invalid_or_missing_log'
            else:
                row['raw_status'] = record['status']
                if record.get('critical_suspected'):
                    row['status'] = 'critical_suspected'
                elif task.stage == 'generation':
                    row['status'] = 'review_required'
                    try:
                        if review is not None and review(record) is True:
                            row['status'] = 'executed'
                    except KeyboardInterrupt:
                        row['status'] = 'interrupted'
                        interrupted = True
                    except Exception as exc:
                        row.update(status='review_error', error_type=type(exc).__name__)
        row['artifact_check'] = 'matched' if expected_hashes else 'not_requested'
        try:
            for name, expected in expected_hashes.items():
                if digest(Path(name)) != expected:
                    raise ValueError('artifact changed during worker or review')
        except (OSError, ValueError):
            row['artifact_check'] = 'mismatch'
            row['prior_status'] = row['status']
            row['status'] = 'artifact_changed'
        if row['status'] != 'executed':
            report['state'] = 'paused'
        _save(ledger, report)
        if interrupted:
            raise KeyboardInterrupt
        if report['state'] == 'paused':
            return report
    report['state'] = 'completed_execution_not_quality_pass'
    _save(ledger, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    prep = commands.add_parser('prepare', help='create a private draft, never freeze/run')
    prep.add_argument('--cases', type=Path, required=True)
    prep.add_argument('--gold', type=Path, required=True)
    prep.add_argument('--output', type=Path, required=True)
    prep.add_argument('--seed', type=int, default=20261119)
    prep.add_argument('--artifact', type=Path, action='append', default=[])
    check = commands.add_parser('verify', help='check draft snapshots, not run readiness')
    check.add_argument('bundle', type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'prepare':
            m = prepare(args.cases, args.gold, args.output, args.seed, tuple(args.artifact))
        else:
            m = verify_bundle(args.bundle)
    except (OSError, ValueError, KeyError, TypeError):
        print('FAIL: invalid input, integrity mismatch or unavailable output; no run authorized')
        return 2
    print(f"PASS: {m['case_count']} cases, {m['planned_primary_attempts']} planned attempts; DRAFT NOT FROZEN, NOT RUN")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
