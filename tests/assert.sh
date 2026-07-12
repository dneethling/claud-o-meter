#!/bin/bash
# Minimal assertion helpers for bash unit tests. Source this, call asserts, call finish.
TESTS_RUN=0
TESTS_FAILED=0

assert_eq() {
  # assert_eq <actual> <expected> <description>
  TESTS_RUN=$((TESTS_RUN + 1))
  if [ "$1" != "$2" ]; then
    echo "FAIL: $3"
    echo "      expected: '$2'"
    echo "      got:      '$1'"
    TESTS_FAILED=$((TESTS_FAILED + 1))
  fi
}

finish() {
  echo "----"
  echo "$TESTS_RUN assertions, $TESTS_FAILED failed"
  [ "$TESTS_FAILED" -eq 0 ]
}
