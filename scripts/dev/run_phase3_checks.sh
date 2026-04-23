#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8096}"
USERNAME="${USERNAME:-}"
APP_PASSWORD="${APP_PASSWORD:-}"
SELECTIONS="${SELECTIONS:-3}"
SEARCH_LIMIT="${SEARCH_LIMIT:-5}"
PARENT_MIN_CANDIDATES="${PARENT_MIN_CANDIDATES:-2}"
QUERY="${QUERY:-abc song short}"
SELECTION_MODE="${SELECTION_MODE:-search-only}"
NO_UPLOAD="${NO_UPLOAD:-1}"
SELECTION_TIMEOUT="${SELECTION_TIMEOUT:-120}"
SIBLING_STATUS_TIMEOUT="${SIBLING_STATUS_TIMEOUT:-180}"
JOB_TIMEOUT="${JOB_TIMEOUT:-900}"
POLL_INTERVAL="${POLL_INTERVAL:-2.0}"
JOB_POLL_INTERVAL="${JOB_POLL_INTERVAL:-2.0}"
PARENT_TERMINAL_STATUSES="${PARENT_TERMINAL_STATUSES:-awaiting_selection,failed}"
ALLOWED_SIBLING_STATUSES="${ALLOWED_SIBLING_STATUSES:-queued_download,downloading,waiting_upload,uploading,done}"
EXTRA_ARGS="${EXTRA_ARGS:-}"
DRY_RUN="${DRY_RUN:-}"
SUMMARY_PARSE="${SUMMARY_PARSE:-}"
SUMMARY_PROFILE="${SUMMARY_PROFILE:-}"
SUMMARY_PARSE_DEFAULT="${SUMMARY_PARSE_DEFAULT:-}"
SUMMARY_REQUIRE_OK="${SUMMARY_REQUIRE_OK:-1}"
SUMMARY_KEY="${SUMMARY_KEY:-}"
SUMMARY_REQUIRE_KEYS="${SUMMARY_REQUIRE_KEYS:-}"
SUMMARY_REQUIRE_KEY_EQUALS="${SUMMARY_REQUIRE_KEY_EQUALS:-}"
SUMMARY_REQUIRE_KEY_TYPES="${SUMMARY_REQUIRE_KEY_TYPES:-}"
SUMMARY_REQUIRE_KEY_REGEX="${SUMMARY_REQUIRE_KEY_REGEX:-}"
SUMMARY_REQUIRE_PATH_PREFIXES="${SUMMARY_REQUIRE_PATH_PREFIXES:-}"
SUMMARY_RAW="${SUMMARY_RAW:-}"
SUMMARY_PARSER_FLAGS="${SUMMARY_PARSER_FLAGS:-}"
SUMMARY_MIN_SCHEMA_VERSION="${SUMMARY_MIN_SCHEMA_VERSION:-0}"
SUMMARY_MIN_KEY_COUNT="${SUMMARY_MIN_KEY_COUNT:-0}"
SUMMARY_VERIFY_KEY_COUNT="${SUMMARY_VERIFY_KEY_COUNT:-}"
SUMMARY_VERIFY_DIGEST="${SUMMARY_VERIFY_DIGEST:-}"
SUMMARY_PICK="${SUMMARY_PICK:-last}"
SUMMARY_PICK_INDEX="${SUMMARY_PICK_INDEX:-}"
SUMMARY_COUNT_ONLY="${SUMMARY_COUNT_ONLY:-}"
SUMMARY_EXPECT_COUNT="${SUMMARY_EXPECT_COUNT:-}"
SUMMARY_FAIL_ON_MULTIPLE="${SUMMARY_FAIL_ON_MULTIPLE:-}"
SUMMARY_PRETTY="${SUMMARY_PRETTY:-}"
SUMMARY_PRINT_KEYS="${SUMMARY_PRINT_KEYS:-}"
SUMMARY_PRINT_PATHS="${SUMMARY_PRINT_PATHS:-}"
SUMMARY_PRINT_STATS="${SUMMARY_PRINT_STATS:-}"
SUMMARY_EMIT_ENVELOPE="${SUMMARY_EMIT_ENVELOPE:-}"
SUMMARY_OUTPUT_PATH="${SUMMARY_OUTPUT_PATH:-}"
OUTPUT_DIR="${OUTPUT_DIR:-}"
SUMMARY_REPORT="${SUMMARY_REPORT:-}"
KEEP_LOG="${KEEP_LOG:-}"
LOG_PATH="${LOG_PATH:-}"
declare -a cmd=()
declare -a extra_args=()

require_app_password() {
  if [[ -n "$APP_PASSWORD" ]]; then
    return
  fi
  echo "APP_PASSWORD is required" >&2
  exit 1
}

require_positive_int() {
  local name="$1"
  local value="$2"
  if [[ "$value" =~ ^[0-9]+$ ]] && (( value >= 1 )); then
    return
  fi
  echo "$name must be an integer >= 1" >&2
  exit 1
}

validate_numeric_env() {
  require_positive_int "SELECTION_TIMEOUT" "$SELECTION_TIMEOUT"
  require_positive_int "SIBLING_STATUS_TIMEOUT" "$SIBLING_STATUS_TIMEOUT"
  require_positive_int "JOB_TIMEOUT" "$JOB_TIMEOUT"
  require_positive_int "SELECTIONS" "$SELECTIONS"
  require_positive_int "SEARCH_LIMIT" "$SEARCH_LIMIT"
  require_positive_int "PARENT_MIN_CANDIDATES" "$PARENT_MIN_CANDIDATES"

  if (( SEARCH_LIMIT < SELECTIONS )); then
    echo "SEARCH_LIMIT must be >= SELECTIONS" >&2
    exit 1
  fi
}

has_nonempty_csv_values() {
  local value="$1"
  [[ -n "${value//,/}" ]]
}

validate_poll_interval() {
  if awk "BEGIN {exit !($POLL_INTERVAL > 0)}"; then
    return
  fi
  echo "POLL_INTERVAL must be > 0" >&2
  exit 1
}

validate_job_poll_interval() {
  if awk "BEGIN {exit !($JOB_POLL_INTERVAL > 0)}"; then
    return
  fi
  echo "JOB_POLL_INTERVAL must be > 0" >&2
  exit 1
}

validate_base_url() {
  case "$BASE_URL" in
    http://*|https://*) return ;;
    *)
      echo "BASE_URL must start with http:// or https://" >&2
      exit 1
      ;;
  esac
}

validate_query_env() {
  if [[ -n "${QUERY// }" ]]; then
    return
  fi
  echo "QUERY must be non-empty" >&2
  exit 1
}

validate_selection_mode_env() {
  case "$SELECTION_MODE" in
    full|search-only) return ;;
    *)
      echo "SELECTION_MODE must be 'full' or 'search-only'" >&2
      exit 1
      ;;
  esac
}

validate_status_env() {
  if ! has_nonempty_csv_values "$PARENT_TERMINAL_STATUSES"; then
    echo "PARENT_TERMINAL_STATUSES must include at least one status" >&2
    exit 1
  fi
  if ! has_nonempty_csv_values "$ALLOWED_SIBLING_STATUSES"; then
    echo "ALLOWED_SIBLING_STATUSES must include at least one status" >&2
    exit 1
  fi
}

apply_no_upload_policy() {
  if [[ -z "$NO_UPLOAD" || "$NO_UPLOAD" == "0" ]]; then
    return
  fi
  SELECTION_MODE="search-only"
}

append_csv_expr_if_missing() {
  local existing="$1"
  local expr="$2"
  if [[ -z "$existing" ]]; then
    echo "$expr"
    return
  fi
  if [[ ",$existing," == *",$expr,"* ]]; then
    echo "$existing"
    return
  fi
  echo "$existing,$expr"
}

apply_no_upload_summary_constraints() {
  if [[ -z "$NO_UPLOAD" || "$NO_UPLOAD" == "0" ]]; then
    return
  fi
  SUMMARY_PARSE=1
  SUMMARY_REQUIRE_KEY_EQUALS="$(append_csv_expr_if_missing "$SUMMARY_REQUIRE_KEY_EQUALS" "selection_mode=search-only")"
}

apply_no_upload_policy

require_app_password
validate_base_url
validate_query_env
validate_selection_mode_env
validate_numeric_env
validate_poll_interval
validate_job_poll_interval
validate_status_env

apply_summary_profile() {
  case "${SUMMARY_PROFILE:-}" in
    "" ) return ;;
    strict-v2)
      SUMMARY_PARSE=1
      SUMMARY_REQUIRE_OK=1
      if [[ "${SUMMARY_MIN_SCHEMA_VERSION:-0}" == "0" ]]; then
        SUMMARY_MIN_SCHEMA_VERSION=2
      fi
      if [[ "${SUMMARY_MIN_KEY_COUNT:-0}" == "0" ]]; then
        SUMMARY_MIN_KEY_COUNT=24
      fi
      SUMMARY_VERIFY_KEY_COUNT=1
      SUMMARY_VERIFY_DIGEST=1
      SUMMARY_REQUIRE_KEYS=${SUMMARY_REQUIRE_KEYS:-ok,parent_job,run_id,summary_schema_version,summary_digest,summary_key_count,generated_at_epoch_ms,run_mode,run_options_digest,summary_keys_sorted}
      SUMMARY_REQUIRE_KEY_TYPES=${SUMMARY_REQUIRE_KEY_TYPES:-summary_schema_version:int,summary_key_count:int,duration_ms:int,generated_at_epoch_ms:int,run_mode:str,run_options_digest:str,summary_keys_sorted:list}
      if [[ -z "${SUMMARY_REQUIRE_KEY_REGEX:-}" ]]; then
        SUMMARY_REQUIRE_KEY_REGEX='summary_digest=^[0-9a-f]{16}$,run_options_digest=^[0-9a-f]{16}$'
      fi
      SUMMARY_REQUIRE_PATH_PREFIXES=${SUMMARY_REQUIRE_PATH_PREFIXES:-summary_keys_sorted,sibling_statuses}
      ;;
    *)
      echo "Unknown SUMMARY_PROFILE: $SUMMARY_PROFILE" >&2
      exit 1
      ;;
  esac
}

apply_summary_profile
apply_no_upload_summary_constraints

build_strict_default_flags() {
  strict_flags=(
    --strict-sibling-statuses
    --strict-url-coverage
    --json-output
  )
}

build_base_cmd() {
  local script_dir
  script_dir="$(dirname "$0")"
  build_strict_default_flags
  cmd=(
    python3 "$script_dir/frontend_results_checks.py"
    --base "$BASE_URL"
    --username "$USERNAME"
    --password "$APP_PASSWORD"
    --query "$QUERY"
    --selection-mode "$SELECTION_MODE"
    --search-limit "$SEARCH_LIMIT"
    --selections "$SELECTIONS"
    --parent-min-candidates "$PARENT_MIN_CANDIDATES"
    --selection-timeout "$SELECTION_TIMEOUT"
    --sibling-status-timeout "$SIBLING_STATUS_TIMEOUT"
    --job-timeout "$JOB_TIMEOUT"
    --poll-interval "$POLL_INTERVAL"
    --job-poll-interval "$JOB_POLL_INTERVAL"
    --parent-terminal-statuses "$PARENT_TERMINAL_STATUSES"
    --allowed-sibling-statuses "$ALLOWED_SIBLING_STATUSES"
    "${strict_flags[@]}"
  )
}

ensure_base_command() {
  if [[ ${#cmd[@]} -gt 0 ]]; then
    return
  fi
  build_base_cmd
}

sanitize_extra_args_for_no_upload() {
  local -n _args_ref=$1
  if [[ -z "$NO_UPLOAD" || "$NO_UPLOAD" == "0" ]]; then
    return
  fi

  local sanitized=()
  local skip_next=0
  for arg in "${_args_ref[@]}"; do
    if [[ $skip_next -eq 1 ]]; then
      skip_next=0
      continue
    fi
    if [[ "$arg" == "--selection-mode" ]]; then
      skip_next=1
      continue
    fi
    sanitized+=("$arg")
  done
  _args_ref=("${sanitized[@]}")
}

parse_extra_args_array() {
  if [[ -z "$EXTRA_ARGS" ]]; then
    extra_args=()
    return
  fi
  # shellcheck disable=SC2206
  extra_args=( $EXTRA_ARGS )
  sanitize_extra_args_for_no_upload extra_args
}

append_extra_args() {
  local -n _cmd_ref=$1
  parse_extra_args_array
  if [[ ${#extra_args[@]} -eq 0 ]]; then
    return
  fi
  _cmd_ref+=("${extra_args[@]}")
}

dedupe_repeatable_flags() {
  local -n _cmd_ref=$1
  local deduped=()
  local -A seen=(
    [--json-output]=0
    [--strict-sibling-statuses]=0
    [--strict-url-coverage]=0
  )

  for arg in "${_cmd_ref[@]}"; do
    if [[ -n "${seen[$arg]+x}" ]]; then
      [[ ${seen[$arg]} -eq 1 ]] && continue
      seen[$arg]=1
    fi
    deduped+=("$arg")
  done

  _cmd_ref=("${deduped[@]}")
}

build_preflight_summary() {
  echo "BASE_URL=$BASE_URL SELECTION_MODE=$SELECTION_MODE NO_UPLOAD=${NO_UPLOAD:-0} SEARCH_LIMIT=$SEARCH_LIMIT SELECTIONS=$SELECTIONS PARENT_MIN_CANDIDATES=$PARENT_MIN_CANDIDATES POLL_INTERVAL=$POLL_INTERVAL DRY_RUN=${DRY_RUN:-0} SUMMARY_PROFILE=${SUMMARY_PROFILE:-<none>} SUMMARY_PARSE=${SUMMARY_PARSE:-0} SUMMARY_REQUIRE_OK=${SUMMARY_REQUIRE_OK:-1} SUMMARY_KEY=${SUMMARY_KEY:-} SUMMARY_PICK_INDEX=${SUMMARY_PICK_INDEX:-<none>} KEEP_LOG=${KEEP_LOG:-0}"
}

build_preflight_json() {
  local dry_run_bool=false
  local summary_parse_bool=false
  [[ -n "$DRY_RUN" ]] && dry_run_bool=true
  [[ -n "$SUMMARY_PARSE" ]] && summary_parse_bool=true
  printf '{"base_url":"%s","search_limit":%s,"selections":%s,"parent_min_candidates":%s,"poll_interval":%s,"dry_run":%s,"summary_parse":%s}' \
    "$BASE_URL" "$SEARCH_LIMIT" "$SELECTIONS" "$PARENT_MIN_CANDIDATES" "$POLL_INTERVAL" \
    "$dry_run_bool" "$summary_parse_bool"
}

print_preflight_if_enabled() {
  if [[ -z "${PRINT_CMD:-}" ]]; then
    return
  fi
  echo "Preflight: $(build_preflight_summary)"
  echo "Summary parser mode: pick=$SUMMARY_PICK pick_index=${SUMMARY_PICK_INDEX:-<none>} key=${SUMMARY_KEY:-<none>} require_keys=${SUMMARY_REQUIRE_KEYS:-<none>} require_key_equals=${SUMMARY_REQUIRE_KEY_EQUALS:-<none>} require_key_types=${SUMMARY_REQUIRE_KEY_TYPES:-<none>} require_key_regex=${SUMMARY_REQUIRE_KEY_REGEX:-<none>} require_path_prefixes=${SUMMARY_REQUIRE_PATH_PREFIXES:-<none>} expect_count=${SUMMARY_EXPECT_COUNT:-<none>} verify_key_count=${SUMMARY_VERIFY_KEY_COUNT:-0} verify_digest=${SUMMARY_VERIFY_DIGEST:-0} print_keys=${SUMMARY_PRINT_KEYS:-0} print_paths=${SUMMARY_PRINT_PATHS:-0} print_stats=${SUMMARY_PRINT_STATS:-0} emit_envelope=${SUMMARY_EMIT_ENVELOPE:-0} output=${SUMMARY_OUTPUT_PATH:-${OUTPUT_DIR:-<stdout>}}"
  if [[ -n "${PRINT_PREFLIGHT_JSON:-}" ]]; then
    echo "Preflight JSON: $(build_preflight_json)"
  fi
}

print_command_if_enabled() {
  local -n _cmd_ref=$1
  if [[ -z "${PRINT_CMD:-}" ]]; then
    return
  fi
  printf 'Running command:'
  printf ' %q' "${_cmd_ref[@]}"
  printf '\n'
}

run_checks_command() {
  local -n _cmd_ref=$1
  if [[ -n "$DRY_RUN" ]]; then
    return
  fi
  "${_cmd_ref[@]}"
}

run_checks_with_capture() {
  local -n _cmd_ref=$1
  local log_path="$2"
  if [[ -n "$DRY_RUN" ]]; then
    : > "$log_path"
    return
  fi
  "${_cmd_ref[@]}" | tee "$log_path"
}

print_log_path_if_enabled() {
  local log_path="$1"
  if [[ -z "${PRINT_LOG_PATH:-}" ]]; then
    return
  fi
  echo "Log path: $log_path"
}

print_summary_report_if_enabled() {
  local log_path="$1"
  if [[ -z "$SUMMARY_REPORT" ]]; then
    return
  fi
  local parser
  parser="$(get_summary_parser_path)"
  local parent
  local schema
  local mode
  local duration

  parent="$($parser "$log_path" --require-ok --print-key parent_job 2>/dev/null || echo null)"
  schema="$($parser "$log_path" --require-ok --print-key summary_schema_version 2>/dev/null || echo null)"
  mode="$($parser "$log_path" --require-ok --print-key selection_mode 2>/dev/null || echo null)"
  duration="$($parser "$log_path" --require-ok --print-key duration_ms 2>/dev/null || echo null)"

  echo "Summary report: parent_job=$parent schema=$schema mode=$mode duration_ms=$duration"
}

get_summary_parser_path() {
  echo "$(dirname "$0")/parse_phase3_summary.py"
}

maybe_parse_summary_from_log() {
  local log_path="$1"
  if [[ -z "$SUMMARY_PARSE" ]]; then
    return
  fi
  local parser
  parser="$(get_summary_parser_path)"

  local parser_args=("$log_path" --pick "$SUMMARY_PICK")
  if [[ -n "$SUMMARY_PICK_INDEX" ]]; then
    parser_args+=(--pick-index "$SUMMARY_PICK_INDEX")
  fi
  if [[ "${SUMMARY_REQUIRE_OK:-1}" != "0" ]]; then
    parser_args+=(--require-ok)
  fi
  if [[ -n "$SUMMARY_REQUIRE_KEYS" ]]; then
    parser_args+=(--require-keys "$SUMMARY_REQUIRE_KEYS")
  fi
  if [[ -n "$SUMMARY_REQUIRE_KEY_EQUALS" ]]; then
    parser_args+=(--require-key-equals "$SUMMARY_REQUIRE_KEY_EQUALS")
  fi
  if [[ -n "$SUMMARY_REQUIRE_KEY_TYPES" ]]; then
    parser_args+=(--require-key-types "$SUMMARY_REQUIRE_KEY_TYPES")
  fi
  if [[ -n "$SUMMARY_REQUIRE_KEY_REGEX" ]]; then
    parser_args+=(--require-key-regex "$SUMMARY_REQUIRE_KEY_REGEX")
  fi
  if [[ -n "$SUMMARY_REQUIRE_PATH_PREFIXES" ]]; then
    parser_args+=(--require-path-prefixes "$SUMMARY_REQUIRE_PATH_PREFIXES")
  fi
  if [[ "${SUMMARY_MIN_SCHEMA_VERSION:-0}" != "0" ]]; then
    parser_args+=(--min-schema-version "$SUMMARY_MIN_SCHEMA_VERSION")
  fi
  if [[ "${SUMMARY_MIN_KEY_COUNT:-0}" != "0" ]]; then
    parser_args+=(--min-key-count "$SUMMARY_MIN_KEY_COUNT")
  fi
  if [[ -n "$SUMMARY_VERIFY_KEY_COUNT" ]]; then
    parser_args+=(--verify-key-count)
  fi
  if [[ -n "$SUMMARY_VERIFY_DIGEST" ]]; then
    parser_args+=(--verify-digest)
  fi
  if [[ -n "$SUMMARY_KEY" ]]; then
    parser_args+=(--print-key "$SUMMARY_KEY")
  elif [[ -n "$SUMMARY_RAW" ]]; then
    : # keep full JSON output from parser
  fi

  if [[ -n "$SUMMARY_COUNT_ONLY" ]]; then
    parser_args+=(--count-only)
  fi
  if [[ -n "$SUMMARY_EXPECT_COUNT" ]]; then
    parser_args+=(--expect-count "$SUMMARY_EXPECT_COUNT")
  fi
  if [[ -n "$SUMMARY_FAIL_ON_MULTIPLE" ]]; then
    parser_args+=(--fail-on-multiple)
  fi
  if [[ -n "$SUMMARY_PRETTY" ]]; then
    parser_args+=(--pretty)
  fi
  if [[ -n "$SUMMARY_PRINT_KEYS" ]]; then
    parser_args+=(--print-keys)
  fi
  if [[ -n "$SUMMARY_PRINT_PATHS" ]]; then
    parser_args+=(--print-paths)
  fi
  if [[ -n "$SUMMARY_PRINT_STATS" ]]; then
    parser_args+=(--print-stats)
  fi
  if [[ -n "$SUMMARY_EMIT_ENVELOPE" ]]; then
    parser_args+=(--emit-envelope)
  fi

  local summary_output
  summary_output="$(resolve_summary_output_path)"
  if [[ -n "$summary_output" ]]; then
    parser_args+=(--output "$summary_output")
  fi

  if [[ -n "$SUMMARY_PARSER_FLAGS" ]]; then
    # shellcheck disable=SC2206
    local extra_parser=( $SUMMARY_PARSER_FLAGS )
    parser_args+=("${extra_parser[@]}")
  fi

  "$parser" "${parser_args[@]}"

  if [[ -n "$summary_output" && -n "${PRINT_CMD:-}" ]]; then
    echo "Summary output written to: $summary_output"
  fi
}

resolve_log_path() {
  if [[ -n "$LOG_PATH" ]]; then
    echo "$LOG_PATH"
    return
  fi
  mktemp
}

resolve_summary_output_path() {
  if [[ -n "$SUMMARY_OUTPUT_PATH" ]]; then
    echo "$SUMMARY_OUTPUT_PATH"
    return
  fi
  if [[ -n "$OUTPUT_DIR" ]]; then
    mkdir -p "$OUTPUT_DIR"
    echo "$OUTPUT_DIR/summary.json"
    return
  fi
  echo ""
}

cleanup_log_if_needed() {
  local log_path="$1"
  if [[ -n "$KEEP_LOG" || -n "$LOG_PATH" ]]; then
    return
  fi
  rm -f "$log_path"
}

run_phase3_checks() {
  ensure_base_command
  append_extra_args cmd
  dedupe_repeatable_flags cmd
  print_preflight_if_enabled
  print_command_if_enabled cmd

  local log_path
  log_path="$(resolve_log_path)"
  trap 'cleanup_log_if_needed "$log_path"' RETURN

  print_log_path_if_enabled "$log_path"
  run_checks_with_capture cmd "$log_path"
  maybe_parse_summary_from_log "$log_path"
  print_summary_report_if_enabled "$log_path"
}

run_phase3_checks
