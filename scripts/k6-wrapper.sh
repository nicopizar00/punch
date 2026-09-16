#!/bin/sh
# Resolves SCENARIO (if set) to a baked-in script path, then execs k6.
# With no SCENARIO, forwards argv untouched -- identical to today's direct
# `k6 "$@"` invocation.
set -e

SCRIPTS_DIR="${K6_SCRIPTS_DIR:-/scripts}"

echo "👊 Punch k6-wrapper" >&2

resolve_script() {
  case "$1" in
    smoke) echo "${SCRIPTS_DIR}/smoke.js" ;;
    gate) echo "${SCRIPTS_DIR}/catalog-gate.js" ;;
    journey) echo "${SCRIPTS_DIR}/order-journey.js" ;;
    bff-checkout-journey) echo "${SCRIPTS_DIR}/bff-checkout-journey.js" ;;
    *) return 1 ;;
  esac
}

if [ -n "${SCENARIO:-}" ]; then
  if ! script_path=$(resolve_script "$SCENARIO"); then
    echo "❌ k6-wrapper: unknown SCENARIO '${SCENARIO}' (expected: smoke, gate, journey, bff-checkout-journey)" >&2
    exit 1
  fi

  if [ "$#" -ge 2 ]; then
    echo "⚠️  SCENARIO=${SCENARIO} overrides requested script '$2' -> ${script_path}" >&2
  else
    echo "ℹ️  SCENARIO=${SCENARIO} -> ${script_path}" >&2
  fi

  if [ "$#" -eq 0 ]; then
    set -- run "$script_path"
  elif [ "$#" -eq 1 ]; then
    set -- "$1" "$script_path"
  else
    subcommand="$1"
    shift 2
    set -- "$subcommand" "$script_path" "$@"
  fi
else
  echo "ℹ️  no SCENARIO set, using script from compose command" >&2
fi

echo "✅ launching: k6 $*" >&2
exec k6 "$@"
