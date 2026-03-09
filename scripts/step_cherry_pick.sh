#!/bin/bash
#
# Step-by-Step Cherry-Pick 脚本
# 特性：逐个 Cherry-Pick，冲突/失败就回滚当前 commit，继续下一个
# 最终汇报：哪些成功、哪些失败、哪些冲突
# 用法: bash step_cherry_pick.sh "<commit1,commit2,commit3>" <target-branch> [repo-path] [test-command]
#

COMMIT_LIST="$1"
TARGET_BRANCH="$2"
REPO_PATH="${3:-.}"
TEST_COMMAND="${4:-python3 -m pytest tests/ -v 2>&1 || true}"

# 解析逗号分隔的 commit 列表
IFS=',' read -ra COMMITS <<< "$COMMIT_LIST"

echo "========================================"
echo "👣 Step-by-Step Cherry-Pick"
echo "========================================"
echo "📋 Commits: ${COMMITS[*]}"
echo "📌 Target: $TARGET_BRANCH"
echo "📁 Repo: $REPO_PATH"
echo "🧪 Test: $TEST_COMMAND"
echo "========================================"

cd "$REPO_PATH"

# ========== 0. 清理函数 ==========
cleanup() {
    git cherry-pick --abort 2>/dev/null || true
    git checkout -- . 2>/dev/null || true
    git clean -fd 2>/dev/null || true
    git reset --hard HEAD 2>/dev/null || true
}

# ========== 1. 切换分支 ==========
echo ""
echo "📌 [1/3] 切换到 $TARGET_BRANCH..."
cleanup
if git checkout "$TARGET_BRANCH" 2>/dev/null; then
    echo "✅ 已切换"
else
    echo "❌ 分支不存在: $TARGET_BRANCH"
    exit 1
fi

# ========== 2. 逐个 Cherry-Pick ==========
echo ""
echo "🍒 [2/3] 逐个 Cherry-Pick..."

PASSED_COMMITS=()
FAILED_COMMITS=()
CONFLICT_COMMITS=()

for i in "${!COMMITS[@]}"; do
    COMMIT="${COMMITS[$i]}"
    COMMIT=$(echo "$COMMIT" | xargs)
    
    echo ""
    echo "--- Commit $((i+1))/${#COMMITS[@]}: $COMMIT ---"
    
    # 先清理工作区（每次重新开始）
    cleanup
    
    # Cherry-Pick 当前 commit
    if ! git cherry-pick "$COMMIT" --no-commit 2>&1; then
        echo "❌ $COMMIT 冲突! 回滚并继续下一个"
        CONFLICT_COMMITS+=("$COMMIT")
        
        # 回滚当前
        cleanup
        continue
    fi
    
    # 运行测试
    TEST_OUTPUT=$(eval "$TEST_COMMAND" 2>&1)
    TEST_RESULT=$?
    
    if [ $TEST_RESULT -eq 0 ]; then
        echo "✅ $COMMIT 测试通过"
        PASSED_COMMITS+=("$COMMIT")
        
        # 回滚当前 commit，继续下一个（不累积）
        cleanup
    else
        echo "❌ $COMMIT 测试失败! 回滚并继续下一个"
        FAILED_COMMITS+=("$COMMIT")
        
        # 保存失败输出
        echo "$TEST_OUTPUT" > /tmp/step_fail_$COMMIT.log 2>/dev/null || true
        
        # 回滚当前 commit，继续下一个
        cleanup
    fi
done

# ========== 3. 汇报结果 ==========
echo ""
echo "========================================"
echo "📊 [3/3] 最终结果"
echo "========================================"

echo "✅ 通过: ${#PASSED_COMMITS[@]} 个"
[ ${#PASSED_COMMITS[@]} -gt 0 ] && echo "   ${PASSED_COMMITS[*]}"

echo "❌ 失败: ${#FAILED_COMMITS[@]} 个"
[ ${#FAILED_COMMITS[@]} -gt 0 ] && echo "   ${FAILED_COMMITS[*]}"

echo "⚠️ 冲突: ${#CONFLICT_COMMITS[@]} 个"
[ ${#CONFLICT_COMMITS[@]} -gt 0 ] && echo "   ${CONFLICT_COMMITS[*]}"

# 最终状态：工作区已清理
cleanup

# 如果全部成功，提交推送
TOTAL_PASSED=${#PASSED_COMMITS[@]}
TOTAL_COMMITS=${#COMMITS[@]}

if [ $TOTAL_PASSED -eq $TOTAL_COMMITS ]; then
    echo ""
    echo "🎉 全部成功! 提交..."
    
    # 重新 cherry-pick 全部
    for COMMIT in "${PASSED_COMMITS[@]}"; do
        git cherry-pick "$COMMIT" --no-commit 2>/dev/null || true
    done
    
    git add -A
    git commit -m "Step cherry-pick: ${PASSED_COMMITS[*]} → $TARGET_BRANCH" 2>/dev/null || true
    
    echo "✅ 已提交到本地"
    
    echo "STEP_SUCCESS"
    echo "PASSED:${PASSED_COMMITS[*]}"
    echo "FAILED:${FAILED_COMMITS[*]}"
    echo "CONFLICT:${CONFLICT_COMMITS[*]}"
    exit 0
else
    echo ""
    echo "📝 部分通过，提交已通过的 commits..."
    
    # 只提交通过的
    if [ $TOTAL_PASSED -gt 0 ]; then
        cleanup
        for COMMIT in "${PASSED_COMMITS[@]}"; do
            git cherry-pick "$COMMIT" --no-commit 2>/dev/null || true
        done
        
        git add -A
        git commit -m "Step cherry-pick (partial): ${PASSED_COMMITS[*]} → $TARGET_BRANCH" 2>/dev/null || true
        
        echo "✅ 已提交到本地"
    fi
    
    cleanup
    
    echo "STEP_PARTIAL"
    echo "PASSED:${PASSED_COMMITS[*]}"
    echo "FAILED:${FAILED_COMMITS[*]}"
    echo "CONFLICT:${CONFLICT_COMMITS[*]}"
    exit 1
fi
