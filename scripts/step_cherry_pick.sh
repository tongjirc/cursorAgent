#!/bin/bash
#
# Step-by-Step Cherry-Pick: for each commit -> cherry-pick -> test -> push
# Failed/conflicted commits are skipped. Push failure reverts that commit.
#

COMMIT_LIST="$1"
TARGET_BRANCH="$2"
REPO_PATH="${3:-.}"
TEST_COMMAND="${4:-python3 -m pytest tests/ -v 2>&1}"

IFS=',' read -ra COMMITS <<< "$COMMIT_LIST"

source "$(dirname "$0")/cp_common.sh"

echo "========================================"
echo "Step-by-Step Cherry-Pick"
echo "========================================"
echo "Commits: ${COMMITS[*]}"
echo "Target: $TARGET_BRANCH"
echo "========================================"

cd "$REPO_PATH" || { echo "ERROR: cannot cd to $REPO_PATH"; exit 1; }
ORIGINAL_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")

# 0. Clean + checkout
clean_workspace

echo "[1/3]"
checkout_branch "$TARGET_BRANCH" || finish 1

# 1. Cherry-pick + test + push each commit
echo ""
echo "[2/3] Cherry-picking commits one by one..."

PASSED_COMMITS=()
FAILED_COMMITS=()
CONFLICT_COMMITS=()
PUSH_FAILED_COMMITS=()

for i in "${!COMMITS[@]}"; do
    COMMIT="${COMMITS[$i]}"
    COMMIT=$(echo "$COMMIT" | xargs)

    echo ""
    echo "========================================"
    echo "[STEP $((i+1))/${#COMMITS[@]}] Cherry-picking: $COMMIT"
    echo "========================================"

    BEFORE_HEAD=$(git rev-parse HEAD)

    do_cherry_pick "$COMMIT" "$BEFORE_HEAD"
    CP_STATUS=$?

    if [ $CP_STATUS -ne 0 ]; then
        CONFLICT_COMMITS+=("$COMMIT")
        echo "Rolling back, continuing to next"
        continue
    fi

    echo "[STEP $((i+1))/${#COMMITS[@]}] Testing: $COMMIT"
    run_tests "$TEST_COMMAND"

    if [ $TEST_RESULT -ne 0 ]; then
        echo "$COMMIT tests FAILED! Rolling back, continuing to next"
        FAILED_COMMITS+=("$COMMIT")
        git reset --hard "$BEFORE_HEAD" 2>/dev/null || true
        continue
    fi

    echo "$COMMIT tests passed"
    echo "[STEP $((i+1))/${#COMMITS[@]}] Pushing: $COMMIT"

    if do_push "$TARGET_BRANCH" "$BEFORE_HEAD"; then
        echo "$COMMIT pushed successfully"
        PASSED_COMMITS+=("$COMMIT")
    else
        echo "$COMMIT push FAILED, continuing to next"
        PUSH_FAILED_COMMITS+=("$COMMIT")
    fi
done

# 2. Summary
echo ""
echo "========================================"
echo "[3/3] Summary"
echo "========================================"

echo "Passed + pushed: ${#PASSED_COMMITS[@]}"
[ ${#PASSED_COMMITS[@]} -gt 0 ] && echo "   ${PASSED_COMMITS[*]}"

echo "Test failed: ${#FAILED_COMMITS[@]}"
[ ${#FAILED_COMMITS[@]} -gt 0 ] && echo "   ${FAILED_COMMITS[*]}"

echo "Conflict: ${#CONFLICT_COMMITS[@]}"
[ ${#CONFLICT_COMMITS[@]} -gt 0 ] && echo "   ${CONFLICT_COMMITS[*]}"

echo "Push failed: ${#PUSH_FAILED_COMMITS[@]}"
[ ${#PUSH_FAILED_COMMITS[@]} -gt 0 ] && echo "   ${PUSH_FAILED_COMMITS[*]}"

TOTAL_PASSED=${#PASSED_COMMITS[@]}
TOTAL_COMMITS=${#COMMITS[@]}

if [ $TOTAL_PASSED -gt 0 ]; then
    print_git_log "$TARGET_BRANCH"
fi

if [ $TOTAL_PASSED -eq $TOTAL_COMMITS ]; then
    echo "STEP_SUCCESS"
elif [ $TOTAL_PASSED -gt 0 ]; then
    echo "STEP_PARTIAL"
else
    echo "STEP_ALL_FAILED"
fi

echo "PASSED:$(IFS=,; echo "${PASSED_COMMITS[*]}")"
echo "FAILED:$(IFS=,; echo "${FAILED_COMMITS[*]}")"
echo "CONFLICT:$(IFS=,; echo "${CONFLICT_COMMITS[*]}")"
echo "PUSH_FAILED:$(IFS=,; echo "${PUSH_FAILED_COMMITS[*]}")"

if [ $TOTAL_PASSED -eq $TOTAL_COMMITS ]; then
    finish 0
else
    finish 1
fi
