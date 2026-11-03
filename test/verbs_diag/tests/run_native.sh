#!/usr/bin/env bash
# Pure CPU test. Mock SDK is ONLY for local tests, never benchmark builds.
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
INCLUDE=${1:-"$HERE/mock_sdk"}
TRACE_TEST_TMP=$(mktemp -d /tmp/uccl-transport-test.XXXXXX)
"${CXX:-g++}" -std=c++17 -O2 -pthread -I "$INCLUDE" -fPIC -shared "$HERE/../transport_trace.cpp" -ldl -o "$TRACE_TEST_TMP/libtrace.so"
"${CXX:-g++}" -std=c++17 -O2 -pthread -I "$INCLUDE" -fPIC -shared "$HERE/trace_fake_provider.cpp" -o "$TRACE_TEST_TMP/libfake.so"
"${CXX:-g++}" -std=c++17 -O2 -pthread -I "$INCLUDE" "$HERE/trace_selftest.cpp" -ldl -o "$TRACE_TEST_TMP/selftest"
"$TRACE_TEST_TMP/selftest" "$TRACE_TEST_TMP/libtrace.so" "$TRACE_TEST_TMP/libfake.so" "$TRACE_TEST_TMP/results.json"
printf 'NATIVE_CPU_SELFTEST=%s\n' "$TRACE_TEST_TMP/results.json"
