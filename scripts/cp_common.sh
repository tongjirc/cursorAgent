#!/bin/bash
#
# Shared functions for cherry-pick scripts.
# Source this file: source "$(dirname "$0")/cp_common.sh"
#

# ---- finish: switch back to original branch and exit ----
finish() {
    local status=$1
    if [ -n "$ORIGINAL_BRANCH" ] && [ "$ORIGINAL_BRANCH" != "$TARGET_BRANCH" ]; then
        echo ""
        echo "Switching back to: $ORIGINAL_BRANCH"
        git checkout "$ORIGINAL_BRANCH" 2>/dev/null || echo "WARNING: cannot switch back to $ORIGINAL_BRANCH"
    fi
    exit "$status"
}

# ---- clean_workspace: reset + clean working tree ----
clean_workspace() {
    echo ""
    echo "Cleaning workspace..."
    git reset --hard HEAD 2>/dev/null || true
    git checkout -- . 2>/dev/null || true
    git clean -fd 2>/dev/null || true
    echo "Workspace clean"
}

# ---- checkout_branch: checkout target with fetch fallback ----
checkout_branch() {
    local branch="$1"
    echo ""
    echo "Checking out $branch..."
    if git checkout "$branch" 2>/dev/null; then
        echo "Switched to $branch"
    else
        echo "$branch not found locally, fetching..."
        git fetch origin "$branch" 2>&1 || true
        if git checkout "$branch" 2>/dev/null || git checkout -b "$branch" "origin/$branch" 2>/dev/null; then
            echo "Switched to $branch (after fetch)"
        else
            echo "ERROR: branch does not exist: $branch"
            return 1
        fi
    fi
}

# ---- do_cherry_pick: cherry-pick a single commit, handle conflict ----
# Returns: 0=success, 1=conflict, 2=empty/no-change
# On conflict: captures diff, aborts, resets to $SAVE_HEAD
do_cherry_pick() {
    local commit="$1"
    local save_head="$2"

    CP_OUTPUT=$(git cherry-pick "$commit" 2>&1)
    CP_RC=$?
    echo "$CP_OUTPUT"

    if [ $CP_RC -eq 0 ]; then
        echo "$commit committed (original message preserved)"
        return 0
    elif echo "$CP_OUTPUT" | grep -q "cherry-pick is now empty"; then
        git cherry-pick --abort 2>/dev/null || git cherry-pick --skip 2>/dev/null || true
        git reset --hard "$save_head" 2>/dev/null || true
        echo "Cherry-pick produced no changes (commit may already exist)"
        return 2
    else
        CONFLICT_FILES=$(git diff --name-only --diff-filter=U 2>/dev/null || true)
        echo "Conflict files: $CONFLICT_FILES"
        echo ""
        echo "CONFLICT_DIFF_START"
        git diff 2>/dev/null || true
        echo "CONFLICT_DIFF_END"

        git cherry-pick --abort 2>/dev/null || true
        git reset --hard "$save_head" 2>/dev/null || true
        return 1
    fi
}

# ---- run_tests: run test command with real-time tee output ----
# Sets TEST_RESULT and TEST_OUTPUT
run_tests() {
    local test_cmd="$1"
    echo ""
    echo "Running tests..."
    TEST_TMPFILE=$(mktemp)
    eval "$test_cmd" 2>&1 | tee "$TEST_TMPFILE"
    TEST_RESULT=${PIPESTATUS[0]}
    TEST_OUTPUT=$(cat "$TEST_TMPFILE")
    rm -f "$TEST_TMPFILE"
}

# ---- do_push: push with force-with-lease, revert on failure ----
# Returns: 0=success, 1=push failed (reverted to save_head)
do_push() {
    local target_branch="$1"
    local save_head="$2"

    echo ""
    echo "Pushing..."
    if git push --force-with-lease origin "HEAD:$target_branch" 2>&1; then
        echo "Push succeeded"
        return 0
    else
        echo "Push FAILED, reverting..."
        git reset --hard "$save_head" 2>/dev/null || true
        echo "Reverted to $save_head"
        return 1
    fi
}

# ---- print_git_log: show recent commits ----
print_git_log() {
    local branch="$1"
    echo ""
    echo "Git Log ($branch):"
    git --no-pager log --graph --pretty=format:'%h -%d %s (%cr) <%an>' --abbrev-commit --date=relative -10 2>/dev/null || true
    echo ""
}
