"""Research-only V06-fix process adapter; no executable CLI or runtime approval."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import time
from typing import Callable

from tools.b210_instrument import (
    ID, MAX_JSON, STAGES, _object, _save, _terminate, digest, verify_bundle,
)

SCRIPT = 'ask_rag_v2_2_b29_v06fix.py'
INDEX_FILES = ('manifest.json', 'chunks.jsonl', 'index.faiss')
PROFILE = {
    'requested_top_k': 1, 'requested_safety_candidate_k': 5,
    'retrieval_mode': 'hybrid', 'max_context_chars': 1100,
    'safety_mode': 'conservative', 'requested_coverage': True,
    'requested_disease_filter': True, 'requested_clinical_priors': True,
    'generation_params': {'temperature': 0, 'enable_thinking': False, 'max_tokens': 128},
}
# These describe script behavior, not rubric verdicts.
BEHAVIOR = {
    'success': ('model_answer', 'model_output', True),
    'safety_abstained_pre_generation': ('abstain', 'safety_template', False),
    'safety_source_scoped_answer_pre_generation': ('limited', 'source_text_extract', False),
    'safety_blocked_post_generation': ('abstain', 'safety_template', True),
    'safety_rewritten_source_extract': ('rewrite', 'source_text_extract', True),
}


@dataclass(frozen=True)
class Invocation:
    case_id: str
    stage: str
    question: str


@dataclass(frozen=True)
class Receipt:
    """Externally authorized references; not certificates verified by this module."""

    run_id: str
    runtime_session: str
    preflight_ref: str
    freeze_ref: str
    authorization_ref: str
    draft_manifest_sha256: str
    model_id: str
    timeout_seconds: float


@dataclass(frozen=True)
class Review:
    reviewer: str
    decision: str  # CONTINUE, PAUSE, or UNRESOLVED (suspected critical harm)
    reason: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip()) and '\x00' not in value


def _check(hashes: dict[Path, str]) -> None:
    for path, expected in hashes.items():
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise ValueError('artifact integrity mismatch')


def _plan(bundle: Path, script: Path, index: Path, receipt: Receipt):
    manifest_path = bundle / 'manifest.json'
    hashes = {manifest_path: receipt.draft_manifest_sha256}
    _check(hashes)
    manifest = verify_bundle(bundle)
    artifacts = {Path(item['path']): item['sha256'] for item in manifest['artifacts']}
    if not {script, *(index / name for name in INDEX_FILES)} <= artifacts.keys():
        raise ValueError('explicit script and all index artifacts must be pinned by the draft')
    hashes.update(artifacts)
    hashes.update({bundle / name: sha for name, sha in manifest['files'].items()})
    _check(hashes)
    cases = _object((bundle / 'cases.json').read_bytes())['cases']
    order = _object((bundle / 'order.json').read_bytes())['ids']
    questions = {row['id']: row['question'] for row in cases}
    if set(questions) != set(order) or not all(map(_text, questions.values())):
        raise ValueError('invalid case input')
    _check(hashes)
    return tuple(Invocation(cid, stage, questions[cid]) for stage in STAGES for cid in order), hashes


def _argv(item: Invocation, interpreter: Path, script: Path, index: Path,
          api_base: str, raw: Path) -> list[str]:
    argv = [str(interpreter), '-B', '-u', str(script), '--index', str(index),
            '--question=' + item.question, '--top-k', '1', '--safety-candidate-k', '5',
            '--retrieval-mode', 'hybrid', '--max-context-chars', '1100',
            '--max-output-tokens', '128', '--safety-mode', 'conservative',
            '--api-base', api_base, '--log', str(raw)]
    if item.stage == 'retrieval':
        argv.extend(('--retrieval-only', '--log-retrieval-only'))
    return argv


def _raw(path: Path) -> tuple[dict, str]:
    if path.is_symlink() or not path.is_file():
        raise ValueError('unsafe or missing raw result')
    with path.open('rb') as stream:
        data = stream.read(MAX_JSON + 1)
    if len(data) > MAX_JSON:
        raise ValueError('oversized raw result')
    lines = data.split(b'\n')
    if len(lines) == 2 and lines[-1] == b'':
        lines.pop()
    if len(lines) != 1:
        raise ValueError('expected one raw record')
    return _object(lines[0]), sha256(data).hexdigest()


def _classify(record: dict, item: Invocation, index: Path, script_sha: str,
              model_id: str) -> dict:
    identity = {'schema_version': '2.8-safety-overlay', 'question': item.question,
                'index_dir': str(index), 'script_sha256': script_sha}
    if any(record.get(k) != v for k, v in (identity | PROFILE).items()):
        raise ValueError('raw identity or profile mismatch')
    status = record.get('status')
    if not isinstance(status, str):
        raise ValueError('invalid raw status')
    if status == 'error':
        return {'outcome': 'worker_reported_error'}
    if not isinstance(record.get('included_evidence'), list) or not isinstance(record.get('retrieved_ids'), list):
        raise ValueError('missing retrieval evidence')
    if item.stage == 'retrieval':
        if status != 'retrieval_only' or any(k in record for k in ('model', 'answer')):
            raise ValueError('retrieval stage mismatch')
        return {'outcome': 'retrieval_only'}
    if status not in BEHAVIOR or not isinstance(record.get('answer'), str):
        raise ValueError('generation status or answer mismatch')
    behavior, origin, api_used = BEHAVIOR[status]
    if record.get('displayed_answer_origin') != origin:
        raise ValueError('answer origin mismatch')
    if (api_used and record.get('model') != model_id) or (not api_used and 'model' in record):
        raise ValueError('API model mismatch')
    return {'outcome': behavior, 'answer_origin': origin, 'model': record.get('model')}


def _worker(argv: list[str], cwd: Path, terminal: Path, timeout: float) -> tuple[str, int | None]:
    env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    with terminal.open('xb') as stream:
        os.chmod(terminal, 0o600)
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            code = proc.wait(timeout=timeout)
            return ('executed' if code == 0 else 'failed'), code
        except subprocess.TimeoutExpired:
            _terminate(proc)
            return 'timeout', proc.returncode
        except BaseException:
            _terminate(proc)
            raise
        finally:
            os.fsync(stream.fileno())


def run(bundle: Path, rag_root: Path, index: Path, interpreter: Path, api_base: str,
        output: Path, receipt: Receipt, *, review: Callable[[dict, Path], Review] | None = None) -> dict:
    """Run a separately authorized primary batch. Caller owns receipt authenticity.

    No retry, restart, clinical judgment or cross-process resume. A review callback
    may wait for a human and continue the same session with a documented decision.
    """
    if os.name != 'posix':
        raise ValueError('POSIX process supervision required')
    match = re.fullmatch(r'http://127\.0\.0\.1:([0-9]{1,5})', api_base)
    if not match or not 1 <= int(match[1]) <= 65535:
        raise ValueError('require an explicit IPv4 loopback HTTP endpoint')
    if (not ID.fullmatch(receipt.run_id) or not all(map(_text, (
            receipt.runtime_session, receipt.preflight_ref, receipt.freeze_ref,
            receipt.authorization_ref, receipt.model_id)))
            or not re.fullmatch(r'[0-9a-f]{64}', receipt.draft_manifest_sha256)):
        raise ValueError('explicit run and approval references required')
    timeout = receipt.timeout_seconds
    if type(timeout) not in (float, int) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('explicit finite positive timeout required')
    bundle, rag_root, index, output = (p.absolute() for p in (bundle, rag_root, index, output))
    if not interpreter.is_absolute():
        raise ValueError('explicit absolute worker interpreter required')
    if any(output.resolve().is_relative_to(p.resolve()) for p in (bundle, rag_root, index)):
        raise ValueError('output must be separate from inputs')
    script = rag_root / SCRIPT
    items, hashes = _plan(bundle, script, index, receipt)
    output.mkdir(mode=0o700, exist_ok=False)
    ledger = output / 'attempts.json'
    rows = [dict(case_id=i.case_id, stage=i.stage, ordinal=n, attempt_type='primary',
                 status='NOT_RUN', quality='NOT_ASSESSED') for n, i in enumerate(items, 1)]
    report = dict(schema_version=1, state='running', next_ordinal=1, started_at=_now(),
                  host=socket.gethostname(), receipt=asdict(receipt), api_base=api_base,
                  cwd=str(rag_root), artifact_expectations={str(p): s for p, s in hashes.items()},
                  automatic_retry=False, automatic_restart=False, attempts=rows)
    _save(ledger, report)
    for item, row in zip(items, rows):
        try:
            _attempt(item, row, report, ledger, hashes, interpreter, script, index, api_base, timeout,
                     receipt.model_id, review)
        except KeyboardInterrupt:
            row.update(status='interrupted', ended_at=_now())
            report.update(state='paused', stop_ordinal=row['ordinal'])
            _save(ledger, report)
            raise
        except OSError as exc:
            row.update(status='instrument_io_error', error_type=type(exc).__name__, ended_at=_now())
        if row['status'] != 'executed':
            report.update(state='paused', stop_ordinal=row['ordinal'], ended_at=_now())
            _save(ledger, report)
            return report
        report['next_ordinal'] = row['ordinal'] + 1 if row is not rows[-1] else None
        if row is rows[-1]:
            report.update(state='completed_execution_not_quality_pass', ended_at=_now())
        _save(ledger, report)
    return report


def _attempt(item, row, report, ledger, hashes, interpreter, script, index, api_base,
             timeout, model_id, review):
    try:
        _check(hashes)
    except (OSError, ValueError):
        row['pause_reason'] = 'artifact_mismatch_before_launch'
        return
    folder = ledger.parent / f'{item.stage}--{item.case_id}'
    folder.mkdir(mode=0o700)
    raw, terminal = folder / 'raw.jsonl', folder / 'stdout.txt'
    argv = _argv(item, interpreter, script, index, api_base, raw)
    start = time.monotonic()
    row.update(status='STARTING', command=argv, started_at=_now(), exit_code=None,
               raw=str(raw), stdout_stderr=str(terminal))
    _save(ledger, report)
    try:
        row['status'], row['exit_code'] = _worker(argv, script.parent, terminal, timeout)
    except OSError as exc:
        row.update(status='worker_io_error', error_type=type(exc).__name__)
    finally:
        row.update(ended_at=_now(), duration_seconds=time.monotonic() - start)
    try:
        record, row['raw_sha256'] = _raw(raw)
        if isinstance(record.get('status'), str):
            row['raw_status'] = record['status']
        row['classification'] = _classify(record, item, index, hashes[script], model_id)
        if row['status'] == 'executed' and row['classification']['outcome'] == 'worker_reported_error':
            row['status'] = 'worker_reported_error'
    except (OSError, ValueError) as exc:
        row['raw_error'] = str(exc)
        if row['status'] == 'executed':
            row['status'] = 'invalid_or_missing_log'
    try:
        _check(hashes)
    except (OSError, ValueError):
        row.update(status='artifact_changed', prior_status=row['status'])
        return
    if row['status'] != 'executed' or item.stage != 'generation':
        return
    row['status'] = 'review_required'
    _save(ledger, report)
    if review is None:
        return
    try:
        decision = review({k: row[k] for k in ('case_id', 'stage', 'ordinal', 'raw_sha256')}, raw)
        if (not isinstance(decision, Review) or not _text(decision.reviewer)
                or not _text(decision.reason) or decision.decision not in ('CONTINUE', 'PAUSE', 'UNRESOLVED')):
            raise ValueError('structured human decision required')
    except Exception as exc:
        row.update(status='review_error', error_type=type(exc).__name__)
        return
    row['review'] = asdict(decision) | {'at': _now()}
    try:
        _check(hashes | {raw: row['raw_sha256']})
    except (OSError, ValueError):
        row['status'] = 'artifact_changed'
        return
    row['status'] = {'CONTINUE': 'executed', 'PAUSE': 'review_paused',
                     'UNRESOLVED': 'critical_unresolved'}[decision.decision]
