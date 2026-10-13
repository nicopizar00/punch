#!/bin/sh
# Punch's Kubernetes step: validate -> preflight -> k6 run -> [DATA] harvest.
#
# Runs as the punch-k6-run WorkflowTemplate's container inside the consumer's
# k6 image (Alpine busybox: sh, awk, tee, tr). Inputs arrive as PUNCH_*
# environment variables; contract: k8s/README.md. Exit codes: k6's own,
# 2 for invalid inputs, 1 for a failed preflight or harvest.
set -eu

workflow=${PUNCH_WORKFLOW:?PUNCH_WORKFLOW is required}
script=${PUNCH_SCRIPT:?PUNCH_SCRIPT is required}
config=${PUNCH_CONFIG:-}
forward=${PUNCH_FORWARD:-}
extra_env=${PUNCH_EXTRA_ENV:-}
requires=${PUNCH_REQUIRES:-}
requires_hint=${PUNCH_REQUIRES_HINT:-}
produces=${PUNCH_PRODUCES:-}
produce=${PUNCH_PRODUCE:-}
data_dir=${PUNCH_DATA_DIR:-/scripts/data}
out_dir=${PUNCH_OUT_DIR:-/punch/out}
options_dir=${PUNCH_OPTIONS_DIR:-/punch/options}

die() {
  code=$1
  shift
  echo "[punch] $*" >&2
  exit "$code"
}

# Value after "<key>=" in a space-separated "<key>=<value>" list.
lookup() {
  for entry in $2; do
    if [ "${entry%%=*}" = "$1" ]; then
      echo "${entry#*=}"
      return 0
    fi
  done
  return 1
}

declared=""
for entry in $produces; do
  declared="$declared ${entry%%=*}"
done

# 0. Validate inputs; nothing runs on a bad request.
opted=""
if [ "$produce" = "all" ]; then
  opted=$declared
else
  for dataset in $(echo "$produce" | tr ',' ' '); do
    lookup "$dataset" "$produces" >/dev/null ||
      die 2 "$workflow does not produce \"$dataset\" (declared:${declared:- none})"
    opted="$opted $dataset"
  done
fi

# The template downloads exactly one dataset file (datasets/<requires>.csv).
set -- $requires
if [ "$#" -gt 1 ]; then
  die 2 "$workflow requires $*; the Kubernetes runtime supports one required dataset per workflow"
fi

set -- run "$script"
if [ -n "$config" ]; then
  set -- "$@" --config "$options_dir/$config.json"
fi
for pair in $extra_env; do
  case $pair in
    ?*=*) ;;
    *) die 2 "extra env \"$pair\" is not NAME=VALUE (values cannot contain spaces)" ;;
  esac
  case " $forward " in
    *" ${pair%%=*} "*) set -- "$@" -e "$pair" ;;
    *) die 2 "$workflow does not forward \"${pair%%=*}\" (forwarded: ${forward:-none})" ;;
  esac
done

# 1. Preflight: every required dataset has at least one row after its header.
for dataset in $requires; do
  file="$data_dir/$dataset.csv"
  rows=0
  if [ -f "$file" ]; then
    rows=$(awk 'NR > 1 && NF { n++ } END { print n + 0 }' "$file")
  fi
  if [ "$rows" -lt 1 ]; then
    producers=$(lookup "$dataset" "$requires_hint" | sed 's/,/, /g')
    die 1 "$workflow requires \"$dataset\"; produce it with:" \
      "${producers:-no known workflow} (--produce $dataset)"
  fi
  export "DATA_$(echo "$dataset" | tr 'a-z-' 'A-Z_')_CSV=$file"
done

# 2. Run k6. Only stdout is teed for harvesting; stderr goes to the pod log.
mkdir -p "$out_dir"
: >"$out_dir/rows.txt"
{
  status=0
  k6 "$@" || status=$?
  echo "$status" >"$out_dir/k6.status"
} | tee "$out_dir/stdout.log"
status=$(cat "$out_dir/k6.status")
if [ "$status" -ne 0 ]; then
  exit "$status"
fi

# 3. Harvest [DATA <dataset>] records. Every record is checked; files are
#    published only after the whole log passes.
staging="$out_dir/.staging"
rm -rf "$staging"
mkdir -p "$staging"
awk -v declared="$produces" -v opted="$opted" -v staging="$staging" '
  BEGIN {
    n = split(declared, entries, " ")
    for (i = 1; i <= n; i++) {
      eq = index(entries[i], "=")
      name = substr(entries[i], 1, eq - 1)
      header[name] = substr(entries[i], eq + 1)
      columns[name] = split(header[name], ignored, ",")
    }
    n = split(opted, names, " ")
    for (i = 1; i <= n; i++) {
      wanted[names[i]] = 1
      rows[names[i]] = 0
    }
  }
  /^\[DATA [a-z0-9]([a-z0-9-]*[a-z0-9])?\]/ {
    close_at = index($0, "]")
    name = substr($0, 7, close_at - 7)
    payload = substr($0, close_at + 1)
    if (substr(payload, 1, 1) == " ") payload = substr(payload, 2)
    if (!(name in columns)) {
      print "[punch] undeclared dataset \"" name "\"" > "/dev/stderr"
      failed = 1
      exit 1
    }
    if (!(name in wanted)) next
    if (split(payload, fields, ",") != columns[name]) {
      print "[punch] " name ": expected " columns[name] " columns, got \"" payload "\"" > "/dev/stderr"
      failed = 1
      exit 1
    }
    file = staging "/" name ".csv"
    if (rows[name] == 0) print header[name] > file
    print payload > file
    rows[name]++
  }
  END {
    if (failed) exit 1
    for (name in wanted) {
      if (rows[name] == 0) {
        print "[punch] no [DATA " name "] records were produced" > "/dev/stderr"
        exit 1
      }
      print name "=" rows[name] > (staging "/rows.txt")
    }
  }
' "$out_dir/stdout.log" || exit 1

if [ -n "$opted" ]; then
  mkdir -p "$out_dir/datasets"
  for dataset in $opted; do
    mv "$staging/$dataset.csv" "$out_dir/datasets/$dataset.csv"
  done
  mv "$staging/rows.txt" "$out_dir/rows.txt"
fi
rm -rf "$staging"
