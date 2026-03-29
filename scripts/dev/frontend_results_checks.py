#!/usr/bin/env python3
import argparse
import hashlib
import json
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone


def api(base: str, path: str, method: str = 'GET', data=None, timeout: int = 60):
    body = None
    headers = {}
    if data is not None:
        body = json.dumps(data).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(base.rstrip('/') + path, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        txt = resp.read().decode('utf-8', errors='replace')
        return json.loads(txt) if txt else {}


def wait_job(base: str, job_id: str, terminal, timeout_s: int = 900, poll_interval_s: float = 2.0):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        jobs = api(base, '/api/jobs')
        j = next((x for x in jobs if x.get('id') == job_id), None)
        if j and str(j.get('status')) in terminal:
            return j
        time.sleep(max(0.2, float(poll_interval_s)))
    raise TimeoutError(job_id)


def get_jobs_by_ids(base: str, job_ids):
    wanted = set(job_ids)
    jobs = api(base, '/api/jobs')
    return {j.get('id'): j for j in jobs if j.get('id') in wanted}


def wait_related_jobs_status(base: str, job_ids, allowed, timeout_s: int = 120, poll_interval_s: float = 2.0):
    t0 = time.time()
    wanted = set(job_ids)
    while time.time() - t0 < timeout_s:
        by_id = {jid: normalize_status(job.get('status')) for jid, job in get_jobs_by_ids(base, wanted).items()}
        if wanted.issubset(set(by_id.keys())) and all(s in allowed for s in by_id.values()):
            return by_id
        time.sleep(max(0.2, float(poll_interval_s)))
    raise TimeoutError(f'related jobs did not reach allowed statuses: {sorted(allowed)}')


def build_selection_payload(index: int, target_url: str):
    return {'index': as_int(index), 'target_url': target_url}


def select_candidate(base: str, parent_job_id: str, index: int, target_url: str, timeout: int = 120):
    return api(
        base,
        f'/api/jobs/{parent_job_id}/select',
        'POST',
        build_selection_payload(index, target_url),
        timeout=timeout,
    )


def run_selections(base: str, parent_job_id: str, indexes, target_url: str, timeout: int):
    results = []
    for idx in indexes:
        res = select_candidate(base, parent_job_id, idx, target_url, timeout=timeout)
        results.append((idx, res))
    return results


def count_selections(selection_results) -> int:
    return len(selection_results or [])


def build_search_payload(query: str, search_limit: int):
    return {'message': query, 'search_limit': as_int(search_limit)}


def fetch_search_parent_job(base: str, query: str, search_limit: int, terminal_statuses=None, timeout_s: int = 900, poll_interval_s: float = 2.0):
    terminal = terminal_statuses or get_default_parent_terminal_statuses()
    sr = api(base, '/api/chat', 'POST', build_search_payload(query, search_limit), timeout=90)
    parent_id = sr['job_id']
    parent = wait_job(base, parent_id, terminal, timeout_s=timeout_s, poll_interval_s=poll_interval_s)
    if parent.get('status') != 'awaiting_selection':
        raise RuntimeError(f"search did not reach awaiting_selection: {parent.get('status')} {parent.get('error')}")
    return parent_id, parent


def get_args_base(args) -> str:
    return str(args.base)


def get_args_query(args) -> str:
    return str(args.query)


def get_args_username(args) -> str:
    return str(args.username)


def get_selection_mode(args) -> str:
    return str(args.selection_mode or 'full').strip().lower()


def is_search_only_mode(args) -> bool:
    return get_selection_mode(args) == 'search-only'


def get_required_selection_count(args) -> int:
    return as_int(args.selections)


def get_search_limit(args) -> int:
    return as_int(args.search_limit)


def get_parent_min_candidates(args) -> int:
    return as_int(args.parent_min_candidates)


def get_selection_timeout(args) -> int:
    return as_int(args.selection_timeout)


def get_sibling_status_timeout(args) -> int:
    return as_int(args.sibling_status_timeout)


def get_job_timeout(args) -> int:
    return as_int(args.job_timeout)


def get_poll_interval(args) -> float:
    return as_float(args.poll_interval)


def get_job_poll_interval(args) -> float:
    return as_float(args.job_poll_interval)


def get_candidates_or_fail(parent_job, minimum: int):
    cands = parent_job.get('candidates') or []
    if len(cands) < as_int(minimum):
        raise RuntimeError(f'Need at least {minimum} candidates for multi-select regression check')
    return cands


def assert_first_selection_on_parent(parent_id: str, selection_results):
    _first_idx, first = selection_results[0]
    if first.get('id') != parent_id:
        raise RuntimeError('First selection should stay on parent search job')


def derive_sibling_ids(parent_id: str, selection_results):
    sibling_ids = []
    seen_ids = {parent_id}
    for i, (_idx, res) in enumerate(selection_results[1:], start=2):
        rid = res.get('id')
        if rid in seen_ids:
            raise RuntimeError(f'Selection #{i} should spawn a distinct sibling job')
        seen_ids.add(rid)
        sibling_ids.append(rid)
    return sibling_ids


def get_parent_candidate_count(parent_after) -> int:
    return len((parent_after or {}).get('candidates') or [])


def assert_parent_candidate_retention(parent_after, minimum: int = 2):
    if not parent_after or not isinstance(parent_after.get('candidates'), list):
        raise RuntimeError('Parent search job should still retain candidate list after selection')
    if get_parent_candidate_count(parent_after) < as_int(minimum):
        raise RuntimeError('Parent search job should still retain candidate list after selection')


def get_job_by_id(rel, job_id: str):
    return next((j for j in rel if j.get('id') == job_id), None)


def validate_sibling_metadata(rel, sibling_ids, target_url: str):
    actual_urls = set()
    for sib_id in sibling_ids:
        sibling_after = get_job_by_id(rel, sib_id)
        if not sibling_after or not sibling_after.get('selected_candidate'):
            raise RuntimeError(f'Sibling job missing selected candidate metadata: {sib_id}')
        if sibling_after.get('target_url') != target_url:
            raise RuntimeError(f'Sibling job target_url mismatch: {sib_id}')
        actual_urls.add((sibling_after.get('selected_candidate') or {}).get('url'))
    return actual_urls


def as_int(value, default: int = 0):
    try:
        return int(value)
    except Exception:
        return int(default)


def as_float(value, default: float = 0.0):
    try:
        return float(value)
    except Exception:
        return float(default)


def as_bool(value, default: bool = False):
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    s = str(value).strip().lower()
    if s in {'1', 'true', 'yes', 'y', 'on'}:
        return True
    if s in {'0', 'false', 'no', 'n', 'off'}:
        return False
    return bool(default)


def normalize_status(value) -> str:
    return str(value or '')


def get_job_status_map(jobs):
    return {j.get('id'): normalize_status(j.get('status')) for j in (jobs or []) if j.get('id')}


def validate_selection_inputs(args):
    if get_selection_mode(args) not in {'full', 'search-only'}:
        raise RuntimeError('--selection-mode must be one of: full, search-only')
    if args.selections < 2:
        raise RuntimeError('--selections must be >= 2')
    if as_int(args.search_limit) < as_int(args.selections):
        raise RuntimeError('--search-limit must be >= --selections')
    if get_parent_min_candidates(args) < 1:
        raise RuntimeError('--parent-min-candidates must be >= 1')


def validate_timing_inputs(args):
    if get_selection_timeout(args) < 1:
        raise RuntimeError('--selection-timeout must be >= 1')
    if get_sibling_status_timeout(args) < 1:
        raise RuntimeError('--sibling-status-timeout must be >= 1')
    if get_job_timeout(args) < 1:
        raise RuntimeError('--job-timeout must be >= 1')
    if get_poll_interval(args) <= 0:
        raise RuntimeError('--poll-interval must be > 0')
    if get_job_poll_interval(args) <= 0:
        raise RuntimeError('--job-poll-interval must be > 0')


def validate_status_inputs(args):
    controls = get_status_controls(args)
    if not controls['parent_terminal_statuses']:
        raise RuntimeError('--parent-terminal-statuses must include at least one status')
    if not controls['allowed_sibling_statuses']:
        raise RuntimeError('--allowed-sibling-statuses must include at least one status')


def validate_search_inputs(args):
    if not get_args_query(args).strip():
        raise RuntimeError('--query must be non-empty')


def ensure_unlocked(base: str, username: str, password: str):
    st = api(base, '/api/setup/status')
    if st.get('unlocked'):
        return
    api(base, '/api/setup/login', 'POST', {'username': username, 'app_password': password})


def get_default_parent_terminal_statuses():
    return {'awaiting_selection', 'failed'}


def get_default_allowed_sibling_statuses():
    return {'queued_download', 'downloading', 'waiting_upload', 'uploading', 'done'}


def to_csv_string(values) -> str:
    return ','.join(sorted(values or []))


def parse_csv_set(value: str, fallback=None):
    raw = (value or '').strip()
    if not raw:
        return set(fallback or [])
    return {part.strip() for part in raw.split(',') if part.strip()}


def parse_terminal_statuses(value: str):
    return parse_csv_set(value, fallback=get_default_parent_terminal_statuses())


def get_target_tonie_url(base: str):
    tonies = api(base, '/api/creative-tonies')
    if not tonies:
        raise RuntimeError('No creative tonies')
    return tonies[0]['edit_url']


def assert_related_job_count(rel, expected_count: int):
    if len(rel) != as_int(expected_count):
        raise RuntimeError(f'Expected {expected_count} related jobs to be present')


def fetch_related_jobs_or_fail(base: str, related_ids):
    rel_by_id = get_jobs_by_ids(base, related_ids)
    rel = list(rel_by_id.values())
    assert_related_job_count(rel, len(related_ids))
    return rel


def get_parent_job(rel, parent_id: str):
    return get_job_by_id(rel, parent_id)


def get_sibling_jobs(rel, sibling_ids):
    return [j for j in (get_job_by_id(rel, sid) for sid in sibling_ids) if j]


def get_strict_sibling_statuses_enabled(args) -> bool:
    return get_strict_flags(args)['strict_sibling_statuses']


def get_strict_url_coverage_enabled(args) -> bool:
    return get_strict_flags(args)['strict_url_coverage']


def build_related_ids(parent_id: str, sibling_ids):
    return {parent_id, *sibling_ids}


def fetch_related_from_selection(base: str, parent_id: str, sibling_ids):
    related_ids = build_related_ids(parent_id, sibling_ids)
    return fetch_related_jobs_or_fail(base, related_ids)


def get_parent_status(status_map, parent_id: str):
    return status_map.get(parent_id)


def get_parent_and_sibling_statuses(rel, parent_id: str, sibling_ids):
    status_map = get_job_status_map(rel)
    return {
        'parent_status': get_parent_status(status_map, parent_id),
        'sibling_statuses': get_sibling_statuses(status_map, sibling_ids),
    }


def build_status_bundle(rel, parent_id: str, sibling_ids):
    return get_parent_and_sibling_statuses(rel, parent_id, sibling_ids)


def get_sibling_statuses(status_map, sibling_ids):
    return {sid: status_map.get(sid) for sid in sibling_ids}


def get_related_counts(parent_after, sibling_ids):
    return {
        'sibling_count': len(sibling_ids),
        'candidate_count_retained': get_parent_candidate_count(parent_after),
    }


def get_flow_metrics(flow_result):
    mode = str(flow_result.get('selection_mode', 'full'))
    return {
        'executed_selection_count': as_int(flow_result.get('executed_selection_count', 0)),
        'duration_ms': as_int(flow_result.get('duration_ms', 0)),
        'selection_mode': mode,
        'no_upload_effective': mode == 'search-only',
    }


def get_utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_epoch_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def get_run_id() -> str:
    return str(uuid.uuid4())


def get_run_identity(args):
    runtime = build_runtime_config(args)
    return {
        'base': runtime['base'],
        'query': runtime['query'],
    }


def get_summary_identity(args, parent_id: str):
    return {
        **get_run_metadata(args),
        'parent_job': parent_id,
    }


def get_run_metadata(args):
    return {
        **get_run_identity(args),
        'generated_at_utc': get_utc_timestamp(),
        'generated_at_epoch_ms': get_epoch_ms(),
        'run_id': get_run_id(),
        'run_mode': 'phase3-regression',
    }


SUMMARY_SCHEMA_VERSION = 3


def get_summary_generation_metadata():
    return {
        'summary_schema_version': SUMMARY_SCHEMA_VERSION,
    }


def get_summary_timing(args):
    return {
        'selection_timeout': get_selection_timeout(args),
        'sibling_status_timeout': get_sibling_status_timeout(args),
        'job_timeout': get_job_timeout(args),
        'poll_interval': get_poll_interval(args),
        'job_poll_interval': get_job_poll_interval(args),
    }


def get_strict_flags(args):
    if is_search_only_mode(args):
        return {
            'strict_sibling_statuses': False,
            'strict_url_coverage': False,
        }
    return {
        'strict_sibling_statuses': as_bool(args.strict_sibling_statuses),
        'strict_url_coverage': as_bool(args.strict_url_coverage),
    }


def get_summary_controls(args):
    return {
        'parent_min_candidates': get_parent_min_candidates(args),
        **get_strict_flags(args),
        **get_sorted_status_controls(args),
    }


def get_sorted_status_controls(args):
    controls = get_status_controls(args)
    return {
        'parent_terminal_statuses': sorted(controls['parent_terminal_statuses']),
        'allowed_sibling_statuses': sorted(controls['allowed_sibling_statuses']),
    }


def get_run_options(args):
    return {
        'selection_mode': get_selection_mode(args),
        'selections': get_required_selection_count(args),
        'search_limit': get_search_limit(args),
        **get_summary_timing(args),
        **get_summary_controls(args),
    }


def build_runtime_config(args):
    return {
        'base': get_args_base(args),
        'username': get_args_username(args),
        'query': get_args_query(args),
        **get_run_options(args),
    }


def get_now_monotonic() -> float:
    return time.monotonic()


def get_elapsed_ms(start: float, end: float) -> int:
    return max(0, int((end - start) * 1000))


def build_flow_result(parent_id: str, sibling_ids, parent_after, executed_selection_count: int, duration_ms: int = 0, selection_mode: str = 'full'):
    return {
        'parent_id': parent_id,
        'sibling_ids': sibling_ids,
        'parent_after': parent_after,
        'executed_selection_count': as_int(executed_selection_count),
        'duration_ms': as_int(duration_ms),
        'selection_mode': str(selection_mode),
    }


def get_flow_identity(flow_result):
    return {
        'parent_id': flow_result['parent_id'],
        'sibling_ids': flow_result['sibling_ids'],
    }


def build_summary_inputs(args, rel, flow_result):
    metrics = get_flow_metrics(flow_result)
    return build_summary_payload(
        args,
        rel,
        flow_result['parent_id'],
        flow_result['sibling_ids'],
        flow_result['parent_after'],
        metrics['executed_selection_count'],
        metrics['duration_ms'],
        metrics['selection_mode'],
    )


def build_summary_payload(args, rel, parent_id: str, sibling_ids, parent_after, executed_selection_count: int = 0, duration_ms: int = 0, selection_mode: str = 'full'):
    return {
        'args': args,
        'rel': rel,
        'parent_id': parent_id,
        'sibling_ids': sibling_ids,
        'parent_after': parent_after,
        'executed_selection_count': executed_selection_count,
        'duration_ms': duration_ms,
        'selection_mode': selection_mode,
    }


SUMMARY_PAYLOAD_KEYS = ('args', 'rel', 'parent_id', 'sibling_ids', 'parent_after', 'executed_selection_count', 'duration_ms', 'selection_mode')


def validate_summary_payload(payload: dict):
    missing = sorted(k for k in SUMMARY_PAYLOAD_KEYS if k not in payload)
    if missing:
        raise RuntimeError(f'Summary payload missing keys: {", ".join(missing)}')


def build_json_summary_from_payload(payload: dict):
    validate_summary_payload(payload)
    return build_json_summary(
        payload['args'],
        payload['rel'],
        payload['parent_id'],
        payload['sibling_ids'],
        payload['parent_after'],
        payload['executed_selection_count'],
        payload['duration_ms'],
        payload['selection_mode'],
    )


def build_json_summary(args, rel, parent_id: str, sibling_ids, parent_after, executed_selection_count: int = 0, duration_ms: int = 0, selection_mode: str = 'full'):
    status_bundle = build_status_bundle(rel, parent_id, sibling_ids)
    flow_metrics = get_flow_metrics({
        'executed_selection_count': executed_selection_count,
        'duration_ms': duration_ms,
        'selection_mode': selection_mode,
    })
    summary = {
        'ok': True,
        **get_summary_generation_metadata(),
        **get_summary_identity(args, parent_id),
        'parent_status': status_bundle['parent_status'],
        'sibling_jobs': sibling_ids,
        'sibling_statuses': status_bundle['sibling_statuses'],
        **get_related_counts(parent_after, sibling_ids),
        **flow_metrics,
        **get_run_options(args),
        'run_options_digest': compute_run_options_digest(args),
    }
    return finalize_summary(summary)


def parse_json_summary_line(line: str):
    prefix = 'JSON_SUMMARY='
    if not isinstance(line, str) or not line.startswith(prefix):
        raise ValueError('line does not start with JSON_SUMMARY=')
    return json.loads(line[len(prefix):])


def require_json_summary(summary: dict):
    if not isinstance(summary, dict):
        raise RuntimeError('JSON summary must be an object')
    if not summary.get('ok'):
        raise RuntimeError('JSON summary is missing ok=true')


def canonical_json(data) -> str:
    return json.dumps(data, sort_keys=True, separators=(',', ':'))


def compute_summary_digest(summary: dict) -> str:
    material = {k: v for k, v in summary.items() if k != 'summary_digest'}
    return hashlib.sha256(canonical_json(material).encode('utf-8')).hexdigest()[:16]


def compute_run_options_digest(args) -> str:
    return hashlib.sha256(canonical_json(get_run_options(args)).encode('utf-8')).hexdigest()[:16]


def get_summary_keys_sorted(summary: dict):
    return sorted(summary.keys())


def finalize_summary(summary: dict):
    summary['summary_key_count'] = len(summary.keys()) + 3
    summary['summary_keys_sorted'] = get_summary_keys_sorted(summary)
    summary['summary_digest'] = compute_summary_digest(summary)
    return summary


def emit_json_summary(summary: dict):
    require_json_summary(summary)
    print('JSON_SUMMARY=' + json.dumps(summary, separators=(',', ':')))


def get_parent_terminal_statuses(value: str = ''):
    return parse_terminal_statuses(value)


def get_allowed_sibling_statuses(value: str = ''):
    return parse_csv_set(value, fallback=get_default_allowed_sibling_statuses())


def get_status_controls(args):
    return {
        'parent_terminal_statuses': get_parent_terminal_statuses(args.parent_terminal_statuses),
        'allowed_sibling_statuses': get_allowed_sibling_statuses(args.allowed_sibling_statuses),
    }


def is_allowed_sibling_status(status: str, allowed_statuses) -> bool:
    return normalize_status(status) in set(allowed_statuses or [])


def should_check_strict_sibling_statuses(args, sibling_ids):
    return get_strict_sibling_statuses_enabled(args) and bool(sibling_ids)


def maybe_wait_for_strict_sibling_statuses(args, sibling_ids, allowed_sibling_statuses):
    if not should_check_strict_sibling_statuses(args, sibling_ids):
        return
    wait_related_jobs_status(
        get_args_base(args),
        sibling_ids,
        allowed_sibling_statuses,
        timeout_s=get_sibling_status_timeout(args),
        poll_interval_s=get_poll_interval(args),
    )


def get_expected_sibling_urls(cands, selected_indexes):
    return {cands[i].get('url') for i in selected_indexes[1:]}


def assert_selection_execution_count(selected_indexes, executed_count: int):
    if as_int(executed_count) != len(selected_indexes):
        raise RuntimeError('Selection execution count mismatch')


def assert_nonempty_selection_plan(selected_indexes):
    if not selected_indexes:
        raise RuntimeError('Selection plan is empty')


def execute_selection_flow(args, parent_id: str, cands, target_url: str):
    selected_indexes = build_selection_indexes(cands, get_required_selection_count(args))
    assert_nonempty_selection_plan(selected_indexes)
    selection_results = run_selections(
        get_args_base(args),
        parent_id,
        selected_indexes,
        target_url,
        timeout=get_selection_timeout(args),
    )
    executed_count = count_selections(selection_results)
    assert_selection_execution_count(selected_indexes, executed_count)
    assert_first_selection_on_parent(parent_id, selection_results)
    sibling_ids = derive_sibling_ids(parent_id, selection_results)
    return selected_indexes, sibling_ids, executed_count


def assert_url_coverage(expected_urls, actual_urls, strict: bool):
    if not strict:
        return
    if expected_urls != actual_urls:
        raise RuntimeError('Sibling selected-candidate URLs do not match requested selections')


def assert_any_sibling_active_or_done(rel, sibling_ids, allowed_statuses):
    sibling_jobs = get_sibling_jobs(rel, sibling_ids)
    if any(is_allowed_sibling_status(j.get('status'), allowed_statuses) for j in sibling_jobs):
        return
    raise RuntimeError('Spawned sibling jobs did not enter an expected active/completed state')


def build_evaluation_payload(parent_id: str, sibling_ids, cands, selected_indexes, target_url: str):
    return {
        'parent_id': parent_id,
        'sibling_ids': sibling_ids,
        'cands': cands,
        'selected_indexes': selected_indexes,
        'target_url': target_url,
    }


def evaluate_results(args, rel, parent_id: str, sibling_ids, cands, selected_indexes, target_url: str):
    controls = get_status_controls(args)
    parent_after = get_parent_job(rel, parent_id)
    assert_parent_candidate_retention(parent_after, minimum=get_parent_min_candidates(args))

    expected_urls = get_expected_sibling_urls(cands, selected_indexes)
    actual_urls = validate_sibling_metadata(rel, sibling_ids, target_url)
    assert_url_coverage(expected_urls, actual_urls, strict=get_strict_url_coverage_enabled(args))

    allowed_sibling_statuses = controls['allowed_sibling_statuses']
    assert_any_sibling_active_or_done(rel, sibling_ids, allowed_sibling_statuses)
    maybe_wait_for_strict_sibling_statuses(args, sibling_ids, allowed_sibling_statuses)

    return parent_after


def build_pass_lines(parent_id: str, sibling_ids, parent_after):
    return [
        'PASS',
        f'parent_job= {parent_id}',
        'sibling_jobs= ' + ' '.join(str(x) for x in sibling_ids),
        f'candidate_count_retained= {get_parent_candidate_count(parent_after)}',
    ]


def print_pass_summary(parent_id: str, sibling_ids, parent_after):
    for line in build_pass_lines(parent_id, sibling_ids, parent_after):
        print(line)


def emit_json_summary_if_enabled(args, rel, flow_result):
    if not args.json_output:
        return
    summary_payload = build_summary_inputs(args, rel, flow_result)
    emit_json_summary(build_json_summary_from_payload(summary_payload))


def get_candidate_indexes(count: int):
    return list(range(max(0, int(count))))


def build_selection_indexes(cands, selections: int):
    primary = pick_short(cands)
    ordered = [primary]
    ordered.extend(i for i in get_candidate_indexes(len(cands)) if i != primary)
    return ordered[:selections]


def pick_short(cands):
    best = 0
    bestd = 10**9
    for i, c in enumerate(cands or []):
        d = int(c.get('duration') or 0)
        if 90 <= d <= 210:
            return i
        if d >= 30 and d < bestd:
            best = i
            bestd = d
    return best


def build_arg_parser():
    p = argparse.ArgumentParser(description='Backend-backed regression checks for search/results multi-select flows')
    p.add_argument('--base', default='http://localhost:8090')
    p.add_argument('--username', default='alex')
    p.add_argument('--password', required=True)
    p.add_argument('--query', default='abc song short')
    p.add_argument('--selection-mode', default='full', help='Selection execution mode: full or search-only.')
    p.add_argument('--selections', type=int, default=3, help='How many candidate selections to attempt from one search (min 2).')
    p.add_argument('--search-limit', type=int, default=5, help='YouTube candidate limit to request from /api/chat.')
    p.add_argument('--parent-min-candidates', type=int, default=2, help='Minimum candidate count parent job must retain after selections.')
    p.add_argument('--strict-sibling-statuses', action='store_true', help='Require all sibling jobs to quickly enter allowed active/completed statuses.')
    p.add_argument('--strict-url-coverage', action='store_true', help='Require every requested sibling candidate URL to map exactly to a spawned sibling job.')
    p.add_argument('--allowed-sibling-statuses', default=to_csv_string(get_default_allowed_sibling_statuses()), help='Comma-separated statuses considered acceptable for sibling activity checks.')
    p.add_argument('--selection-timeout', type=int, default=120, help='Timeout seconds per /select request.')
    p.add_argument('--sibling-status-timeout', type=int, default=180, help='Timeout seconds for strict sibling-status convergence checks.')
    p.add_argument('--job-timeout', type=int, default=900, help='Timeout seconds for parent job reaching terminal statuses.')
    p.add_argument('--poll-interval', type=float, default=2.0, help='Polling interval seconds for status wait loops.')
    p.add_argument('--job-poll-interval', type=float, default=2.0, help='Polling interval seconds for parent job wait loop.')
    p.add_argument('--parent-terminal-statuses', default=to_csv_string(get_default_parent_terminal_statuses()), help='Comma-separated terminal statuses to stop parent-job polling.')
    p.add_argument('--json-output', action='store_true', help='Emit machine-readable JSON summary line in addition to PASS text.')
    return p


def parse_args():
    return build_arg_parser().parse_args()


def build_run_context(args):
    runtime = build_runtime_config(args)
    controls = get_status_controls(args)
    ensure_unlocked(runtime['base'], runtime['username'], args.password)
    return {
        'runtime': runtime,
        'controls': controls,
        'target_url': get_target_tonie_url(runtime['base']),
        'parent_terminal_statuses': controls['parent_terminal_statuses'],
    }


def get_context_runtime(ctx):
    return ctx['runtime']


def get_context_target_url(ctx):
    return ctx['target_url']


def get_context_parent_terminal_statuses(ctx):
    return ctx['parent_terminal_statuses']


def validate_inputs(args):
    validate_selection_inputs(args)
    validate_timing_inputs(args)
    validate_status_inputs(args)
    validate_search_inputs(args)


def bootstrap_search_candidates(args, ctx):
    runtime = get_context_runtime(ctx)
    parent_id, parent = fetch_search_parent_job(
        runtime['base'],
        runtime['query'],
        runtime['search_limit'],
        terminal_statuses=get_context_parent_terminal_statuses(ctx),
        timeout_s=get_job_timeout(args),
        poll_interval_s=get_job_poll_interval(args),
    )
    cands = get_candidates_or_fail(parent, minimum=get_required_selection_count(args))
    return parent_id, parent, cands


def run_selection_session(args, ctx):
    parent_id, _parent, cands = bootstrap_search_candidates(args, ctx)
    selected_indexes, sibling_ids, executed_selection_count = execute_selection_flow(
        args,
        parent_id,
        cands,
        get_context_target_url(ctx),
    )
    rel = fetch_related_from_selection(get_args_base(args), parent_id, sibling_ids)
    eval_payload = build_evaluation_payload(
        parent_id,
        sibling_ids,
        cands,
        selected_indexes,
        get_context_target_url(ctx),
    )
    return {
        'rel': rel,
        'eval_payload': eval_payload,
        'executed_selection_count': executed_selection_count,
    }


def run_search_only_session(args, ctx):
    parent_id, parent, cands = bootstrap_search_candidates(args, ctx)
    assert_parent_candidate_retention(parent, minimum=get_parent_min_candidates(args))
    return {
        'rel': [parent],
        'eval_payload': build_evaluation_payload(parent_id, [], cands, [], get_context_target_url(ctx)),
        'executed_selection_count': 0,
        'search_only': True,
    }


def emit_outputs(args, rel, flow_result):
    identity = get_flow_identity(flow_result)
    print_pass_summary(identity['parent_id'], identity['sibling_ids'], flow_result['parent_after'])
    emit_json_summary_if_enabled(args, rel, flow_result)


def run_check_flow(args):
    started = get_now_monotonic()
    ctx = build_run_context(args)
    session = run_search_only_session(args, ctx) if is_search_only_mode(args) else run_selection_session(args, ctx)

    p = session['eval_payload']
    if session.get('search_only'):
        parent_after = get_parent_job(session['rel'], p['parent_id'])
    else:
        parent_after = evaluate_results(
            args,
            session['rel'],
            p['parent_id'],
            p['sibling_ids'],
            p['cands'],
            p['selected_indexes'],
            p['target_url'],
        )

    elapsed_ms = get_elapsed_ms(started, get_now_monotonic())
    flow_result = build_flow_result(
        p['parent_id'],
        p['sibling_ids'],
        parent_after,
        session['executed_selection_count'],
        duration_ms=elapsed_ms,
        selection_mode=get_selection_mode(args),
    )
    emit_outputs(args, session['rel'], flow_result)


def main():
    args = parse_args()
    validate_inputs(args)
    run_check_flow(args)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print('FAIL:', e)
        sys.exit(1)
