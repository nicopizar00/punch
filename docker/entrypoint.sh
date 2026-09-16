#!/bin/sh
# Container ENTRYPOINT. Stays generic -- all k6 invocation logic lives in
# k6-wrapper.sh.
set -e
exec /scripts/k6-wrapper.sh "$@"
