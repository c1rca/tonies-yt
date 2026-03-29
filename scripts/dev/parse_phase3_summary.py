#!/usr/bin/env python3
import argparse
import hashlib
import json
import re
import sys


def parse_summary_line(line: str):
    prefix = 'JSON_SUMMARY='
    if not line.startswith(prefix):
        return None
    return json.loads(line[len(prefix):])


def parse_any_json_line(line: str):
    txt = (line or '').strip()
    if not txt:
        return None
    if txt.startswith('JSON_SUMMARY='):
        return parse_summary_line(txt)
    if txt.startswith('{') and txt.endswith('}'):
        try:
            return json.loads(txt)
        except Exception:
            return None
    return None


def validate_summary_schema(summary: dict, min_version: int = 1):
    version = int(summary.get('summary_schema_version', 0) or 0)
    if version < int(min_version):
        raise RuntimeError(f'Summary schema version {version} is below required minimum {min_version}')


def canonical_json(data) -> str:
    return json.dumps(data, sort_keys=True, separators=(',', ':'))


def compute_summary_digest(summary: dict) -> str:
    material = {k: v for k, v in summary.items() if k != 'summary_digest'}
    return hashlib.sha256(canonical_json(material).encode('utf-8')).hexdigest()[:16]


def validate_summary_key_count(summary: dict):
    declared = int(summary.get('summary_key_count', 0) or 0)
    actual = len(summary.keys())
    if declared != actual:
        raise RuntimeError(f'Summary key count mismatch: declared {declared}, actual {actual}')


def validate_summary_digest(summary: dict):
    declared = str(summary.get('summary_digest') or '')
    if not declared:
        raise RuntimeError('Missing summary_digest')
    actual = compute_summary_digest(summary)
    if declared != actual:
        raise RuntimeError(f'Summary digest mismatch: declared {declared}, actual {actual}')


def read_text(path: str | None):
    if not path or path == '-':
        return sys.stdin.read()
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def read_lines(path: str | None):
    return read_text(path).splitlines()


def collect_summaries(lines):
    out = []
    for line in lines:
        parsed = parse_any_json_line(line)
        if parsed is not None:
            out.append(parsed)
    return out


def parse_csv_keys(value: str):
    raw = (value or '').strip()
    if not raw:
        return []
    return [k.strip() for k in raw.split(',') if k.strip()]


def parse_csv_values(value: str):
    raw = (value or '').strip()
    if not raw:
        return []
    return [v.strip() for v in raw.split(',') if v.strip()]


def parse_key_value_pairs(value: str):
    pairs = []
    for expr in parse_csv_values(value):
        if '=' not in expr:
            raise RuntimeError(f'Invalid expression (expected key=value): {expr}')
        k, v = expr.split('=', 1)
        pairs.append((k.strip(), v.strip()))
    return pairs


def parse_key_type_pairs(value: str):
    pairs = []
    for expr in parse_csv_values(value):
        if ':' not in expr:
            raise RuntimeError(f'Invalid expression (expected key:type): {expr}')
        k, t = expr.split(':', 1)
        pairs.append((k.strip(), t.strip().lower()))
    return pairs


def parse_expected_count(value: str):
    raw = str(value or '').strip()
    if not raw:
        return None
    return int(raw)


def build_parser_stats(summaries, selected_summary, pick_mode: str, selected_index: int):
    declared_key_count = int((selected_summary or {}).get('summary_key_count', 0) or 0)
    actual_key_count = len((selected_summary or {}).keys())
    return {
        'summary_count': len(summaries or []),
        'pick_mode': pick_mode,
        'selected_index': int(selected_index),
        'selected_has_ok': bool((selected_summary or {}).get('ok')),
        'selected_schema_version': int((selected_summary or {}).get('summary_schema_version', 0) or 0),
        'selected_key_count': actual_key_count,
        'selected_declared_key_count': declared_key_count,
        'selected_key_count_matches_declared': declared_key_count == actual_key_count,
        'selected_has_digest': bool((selected_summary or {}).get('summary_digest')),
        'selected_digest_prefix': str((selected_summary or {}).get('summary_digest', ''))[:8],
    }


def build_output_envelope(summary, stats, source: str):
    return {
        'source': source,
        'summary': summary,
        'stats': stats,
    }


def get_nested_value(obj, dotted_key: str):
    cur = obj
    for part in str(dotted_key).split('.'):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
            continue
        raise KeyError(dotted_key)
    return cur


def collect_key_paths(obj, prefix=''):
    paths = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            paths.append(path)
            paths.extend(collect_key_paths(v, path))
    return paths


def main():
    p = argparse.ArgumentParser(description='Extract and validate JSON_SUMMARY lines from phase-3 check output.')
    p.add_argument('input', nargs='?', default='-', help='Input log path or - for stdin')
    p.add_argument('--require-ok', action='store_true', help='Require ok=true in the parsed summary')
    p.add_argument('--require-keys', default='', help='Comma-separated top-level keys that must exist in summary')
    p.add_argument('--min-schema-version', type=int, default=0, help='Require summary_schema_version >= this value (0 disables check).')
    p.add_argument('--min-key-count', type=int, default=0, help='Require at least this many top-level keys in summary.')
    p.add_argument('--verify-key-count', action='store_true', help='Require summary_key_count to match actual top-level key count.')
    p.add_argument('--verify-digest', action='store_true', help='Require summary_digest to match computed digest.')
    p.add_argument('--require-key-types', default='', help='Comma-separated key:type checks (types: str,int,float,bool,list,dict).')
    p.add_argument('--require-key-regex', default='', help='Comma-separated key=regex checks for string values.')
    p.add_argument('--pick', choices=['first', 'last'], default='last', help='Which summary to use if multiple are present.')
    p.add_argument('--pick-index', type=int, default=-1, help='Pick summary by 0-based index (overrides --pick when >=0).')
    p.add_argument('--count-only', action='store_true', help='Print only the number of JSON_SUMMARY lines found.')
    p.add_argument('--expect-count', default='', help='Require exact number of JSON_SUMMARY lines (empty disables check).')
    p.add_argument('--fail-on-multiple', action='store_true', help='Fail if more than one JSON_SUMMARY line exists.')
    p.add_argument('--pretty', action='store_true', help='Pretty-print JSON output.')
    p.add_argument('--print-keys', action='store_true', help='Print sorted top-level keys for selected summary.')
    p.add_argument('--print-key', default='', help='Optional top-level key to print from the summary')
    p.add_argument('--print-stats', action='store_true', help='Print parser stats object instead of summary payload.')
    p.add_argument('--print-paths', action='store_true', help='Print sorted dotted key paths discovered in selected summary.')
    p.add_argument('--emit-envelope', action='store_true', help='Emit {source,summary,stats} envelope object.')
    p.add_argument('--require-key-equals', default='', help='Comma-separated key=value checks (supports dotted keys).')
    p.add_argument('--require-path-prefixes', default='', help='Comma-separated dotted path prefixes that must exist in summary paths.')
    p.add_argument('--output', default='', help='Optional output file path for parser result (prints to stdout when empty).')
    args = p.parse_args()

    text = read_text(args.input)
    lines = text.splitlines()
    summaries = collect_summaries(lines)

    if not summaries and text.strip().startswith('{'):
        try:
            summaries = [json.loads(text)]
        except Exception:
            pass

    count = len(summaries)
    expected_count = parse_expected_count(args.expect_count)
    if expected_count is not None and count != expected_count:
        raise RuntimeError(f'Expected {expected_count} JSON_SUMMARY lines, found {count}')
    if args.fail_on_multiple and count > 1:
        raise RuntimeError(f'Expected at most 1 JSON_SUMMARY line, found {count}')

    if args.count_only:
        print(count)
        return

    if not summaries:
        raise RuntimeError('No JSON_SUMMARY line found')

    if int(args.pick_index) >= 0:
        idx = int(args.pick_index)
        if idx >= count:
            raise RuntimeError(f'pick-index {idx} is out of range for {count} summaries')
        summary = summaries[idx]
        selected_index = idx
    else:
        if args.pick == 'first':
            summary = summaries[0]
            selected_index = 0
        else:
            summary = summaries[-1]
            selected_index = count - 1

    if args.require_ok and not summary.get('ok'):
        raise RuntimeError('Summary exists but ok is not true')

    if int(args.min_schema_version) > 0:
        validate_summary_schema(summary, min_version=int(args.min_schema_version))
    if int(args.min_key_count) > 0 and len(summary.keys()) < int(args.min_key_count):
        raise RuntimeError(f'Summary key count {len(summary.keys())} is below required minimum {int(args.min_key_count)}')
    if args.verify_key_count:
        validate_summary_key_count(summary)
    if args.verify_digest:
        validate_summary_digest(summary)

    required = parse_csv_keys(args.require_keys)
    for key in required:
        try:
            get_nested_value(summary, key)
        except KeyError:
            raise RuntimeError(f'Missing required summary key: {key}')

    for key, expected in parse_key_value_pairs(args.require_key_equals):
        actual = get_nested_value(summary, key)
        if str(actual) != expected:
            raise RuntimeError(f'Key {key} expected {expected} but got {actual}')

    type_checks = {
        'str': str,
        'int': int,
        'float': (int, float),
        'bool': bool,
        'list': list,
        'dict': dict,
    }
    for key, type_name in parse_key_type_pairs(args.require_key_types):
        if type_name not in type_checks:
            raise RuntimeError(f'Unknown type in require-key-types: {type_name}')
        actual = get_nested_value(summary, key)
        expected_type = type_checks[type_name]
        if not isinstance(actual, expected_type):
            raise RuntimeError(f'Key {key} expected type {type_name} but got {type(actual).__name__}')

    for key, pattern in parse_key_value_pairs(args.require_key_regex):
        actual = str(get_nested_value(summary, key))
        try:
            matched = re.search(pattern, actual)
        except re.error as e:
            raise RuntimeError(f'Invalid regex for key {key}: {pattern} ({e})')
        if not matched:
            raise RuntimeError(f'Key {key} value {actual} does not match regex {pattern}')

    all_paths = sorted(set(collect_key_paths(summary)))
    for prefix in parse_csv_values(args.require_path_prefixes):
        if not any(p == prefix or p.startswith(prefix + '.') for p in all_paths):
            raise RuntimeError(f'Missing required path prefix: {prefix}')

    stats = build_parser_stats(summaries, summary, args.pick, selected_index)

    if args.emit_envelope:
        envelope = build_output_envelope(summary, stats, args.input)
        if args.pretty:
            result = json.dumps(envelope, indent=2, sort_keys=True)
        else:
            result = json.dumps(envelope, separators=(',', ':'))
    elif args.print_stats:
        if args.pretty:
            result = json.dumps(stats, indent=2, sort_keys=True)
        else:
            result = json.dumps(stats, separators=(',', ':'))
    elif args.print_paths:
        result = json.dumps(all_paths, separators=(',', ':'))
    elif args.print_keys:
        result = json.dumps(sorted(summary.keys()), separators=(',', ':'))
    elif args.print_key:
        value = get_nested_value(summary, args.print_key)
        if args.pretty:
            result = json.dumps(value, indent=2, sort_keys=True)
        else:
            result = json.dumps(value, separators=(',', ':'))
    else:
        if args.pretty:
            result = json.dumps(summary, indent=2, sort_keys=True)
        else:
            result = json.dumps(summary, separators=(',', ':'))

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(result + '\n')
    else:
        print(result)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)
