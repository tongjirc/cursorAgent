#!/bin/bash
#
# Revert script: revert one or more commits on target branch
# Single commit: revert -> test -> push
# Multiple commits: revert each -> test once -> push
# Reverts on any failure (test/push).
#

COMMIT_LIST="$1"
TARGET_BRANCH="$2"
REPO_PATH="${3:-.}"
TEST_COMMAND="${4:-python3 -m pytest tests/ -v 2>&1}"

IFS=',' read -ra COMMITS <<< "$COMMIT_LIST"

source "$(dirname "$0")/cp_common.sh"

echo "========================================"
echo "Revert: ${#COMMITS[@]} commits on $TARGET_BRANCH"
echo "========================================"
echo "Commits: ${COMMITS[*]}"
echo "Target: $TARGET_BRANCH"
echo "========================================"

cd "$REPO_PATH" || { echo "ERROR: cannot cd to $REPO_PATH"; exit 1; }
ORIGINAL_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")

# 0. Clean
clean_workspace

# 1. Checkout
echo "[1/4]"
checkout_branch "$TARGET_BRANCH" || finish 1
SAVE_HEAD=$(git rev-parse HEAD)

# 2. Revert each commit
echo ""
echo "[2/4] Reverting ${#COMMITS[@]} commits..."

for i in "${!COMMITS[@]}"; do
    COMMIT="${COMMITS[$i]}"
    COMMIT=$(echo "$COMMIT" | xargs)

    echo "--- Revert $((i+1))/${#COMMITS[@]}: $COMMIT ---"

    if git revert --no-edit "$COMMIT" 2>&1; then
        echo "$COMMIT reverted"
    else
        echo "$COMMIT revert CONFLICT!"
        CONFLICT_FILES=$(git diff --name-only --diff-filter=U 2>/dev/null || true)
        echo "Conflict files: $CONFLICT_FILES"
        echo ""
        echo "CONFLICT_DIFF_START"
        git diff 2>/dev/null || true
        echo "CONFLICT_DIFF_END"

        git revert --abort 2>/dev/null || true
        git reset --hard "$SAVE_HEAD" 2>/dev/null || true
        echo "All rolled back"

        echo "CONFLICT"
        echo "COMMIT:$COMMIT"
        echo "FILES:$CONFLICT_FILES"
        finish 1
    fi
done

echo "All ${#COMMITS[@]} commits reverted"

# 3. Test
echo "[3/4]"
run_tests "$TEST_COMMAND"
if [ $TEST_RESULT -ne 0 ]; then
    echo "Tests FAILED (exit $TEST_RESULT)"
    git reset --hard "$SAVE_HEAD" 2>/dev/null || true
    echo "All rolled back"
    echo "TEST_FAIL"
    echo "COMMITS:$(IFS=,; echo "${COMMITS[*]}")"
    echo "OUTPUT:$TEST_OUTPUT"
    finish 1
fi
echo "Tests passed"
echo "========================================"
echo "Revert succeeded!"
echo "========================================"

# 4. Push
echo "[4/4]"
if do_push "$TARGET_BRANCH" "$SAVE_HEAD"; then
    print_git_log "$TARGET_BRANCH"
    echo "SUCCESS"
    echo "COMMITS:$(IFS=,; echo "${COMMITS[*]}")"
    finish 0
else
    echo "PUSH_FAIL"
    echo "COMMITS:$(IFS=,; echo "${COMMITS[*]}")"
    finish 1
fi
