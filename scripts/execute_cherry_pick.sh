#!/bin/bash
#
# Single Cherry-Pick: cherry-pick -> test -> push
# Preserves original commit message. Reverts on any failure.
#

COMMIT_ID="$1"
TARGET_BRANCH="$2"
REPO_PATH="${3:-.}"
TEST_COMMAND="${4:-python3 -m pytest tests/ -v 2>&1}"

source "$(dirname "$0")/cp_common.sh"

echo "========================================"
echo "Cherry-Pick: $COMMIT_ID -> $TARGET_BRANCH"
echo "========================================"

cd "$REPO_PATH" || { echo "ERROR: cannot cd to $REPO_PATH"; exit 1; }
ORIGINAL_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")

# 0. Clean
clean_workspace

# 1. Checkout
echo "[1/4]"
checkout_branch "$TARGET_BRANCH" || finish 1
SAVE_HEAD=$(git rev-parse HEAD)

# 2. Cherry-pick
echo ""
echo "[2/4] Cherry-picking $COMMIT_ID..."
do_cherry_pick "$COMMIT_ID" "$SAVE_HEAD"
CP_STATUS=$?

if [ $CP_STATUS -eq 2 ]; then
    echo "NO_CHANGE"
    finish 0
elif [ $CP_STATUS -ne 0 ]; then
    echo "CONFLICT"
    echo "FILES:$CONFLICT_FILES"
    finish 2
fi

# 3. Test
echo "[3/4]"
run_tests "$TEST_COMMAND"
if [ $TEST_RESULT -ne 0 ]; then
    echo "Tests FAILED (exit $TEST_RESULT)"
    git reset --hard "$SAVE_HEAD" 2>/dev/null || true
    echo "Rolled back"
    echo "TEST_FAIL"
    echo "OUTPUT:$TEST_OUTPUT"
    finish 1
fi
echo "Tests passed"

# 4. Push
echo "[4/4]"
if do_push "$TARGET_BRANCH" "$SAVE_HEAD"; then
    echo "========================================"
    echo "Done!"
    echo "========================================"
    print_git_log "$TARGET_BRANCH"
    finish 0
else
    echo "PUSH_FAIL"
    finish 1
fi
