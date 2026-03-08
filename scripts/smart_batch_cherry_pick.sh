#!/bin/bash
#
# 智能批量 Cherry-Pick 脚本
# 特性：逐个 Cherry-Pick + 测试，失败后自动定位哪个 commit 导致
# 用法: bash smart_batch_cherry_pick.sh "<commit1,commit2,commit3>" <target-branch> [repo-path] [test-command]
#

COMMIT_LIST="$1"
TARGET_BRANCH="$2"
REPO_PATH="${3:-.}"
TEST_COMMAND="${4:-python3 -m pytest tests/ -v 2>&1 || true}"

# 解析逗号分隔的 commit 列表
IFS=',' read -ra COMMITS <<< "$COMMIT_LIST"

echo "========================================"
echo "🍒 智能批量 Cherry-Pick"
echo "========================================"
echo "📋 Commits: ${COMMITS[*]}"
echo "📌 Target: $TARGET_BRANCH"
echo "📁 Repo: $REPO_PATH"
echo "🧪 Test: $TEST_COMMAND"
echo "========================================"

cd "$REPO_PATH"

# ========== 0. 清理函数 ==========
cleanup() {
    echo "🧹 清理工作区..."
    git cherry-pick --abort 2>/dev/null || true
    git checkout -- . 2>/dev/null || true
    git clean -fd 2>/dev/null || true
    git reset --hard HEAD 2>/dev/null || true
}

# ========== 1. 切换分支 ==========
echo ""
echo "📌 [1/4] 切换到 $TARGET_BRANCH..."
cleanup
if git checkout "$TARGET_BRANCH" 2>/dev/null; then
    echo "✅ 已切换"
else
    echo "❌ 分支不存在: $TARGET_BRANCH"
    exit 1
fi

# ========== 2. 逐个 Cherry-Pick + 测试 ==========
echo ""
echo "🍒 [2/4] 逐个 Cherry-Pick + 测试..."

PASSED_COMMITS=()
FAILED_COMMIT=""

for i in "${!COMMITS[@]}"; do
    COMMIT="${COMMITS[$i]}"
    COMMIT=$(echo "$COMMIT" | xargs)
    
    echo ""
    echo "--- 测试 Commit $((i+1))/${#COMMITS[@]}: $COMMIT ---"
    
    # 先 cherry-pick 这个 commit
    if ! git cherry-pick "$COMMIT" --no-commit 2>&1; then
        echo "❌ $COMMIT 冲突! 跳过"
        continue
    fi
    
    # 运行测试
    TEST_OUTPUT=$(eval "$TEST_COMMAND" 2>&1)
    TEST_RESULT=$?
    
    if [ $TEST_RESULT -eq 0 ]; then
        echo "✅ $COMMIT 测试通过"
        PASSED_COMMITS+=("$COMMIT")
    else
        echo "❌ $COMMIT 测试失败!"
        FAILED_COMMIT="$COMMIT"
        FAILED_OUTPUT="$TEST_OUTPUT"
        
        # 清理这个 commit 的改动
        git cherry-pick --abort 2>/dev/null || true
        git checkout -- . 2>/dev/null || true
        git clean -fd 2>/dev/null || true
        
        break  # 找到第一个失败的 commit，停止
    fi
done

# ========== 3. 结果判定 ==========
echo ""
echo "📊 [3/4] 结果分析..."

if [ -z "$FAILED_COMMIT" ]; then
    # 全部成功
    echo "✅ 全部 ${#COMMITS[@]} 个 commit 测试通过!"
    
    # 提交推送
    echo ""
    echo "📤 提交..."
    git add -A
    git commit -m "Batch cherry-pick: ${PASSED_COMMITS[*]} → $TARGET_BRANCH" 2>/dev/null || true
    
    if git push origin "$TARGET_BRANCH" 2>/dev/null; then
        echo "✅ 推送成功"
    else
        echo "⚠️ 推送失败 (本地OK)"
    fi
    
    echo "SUCCESS"
    echo "COMMITS:${PASSED_COMMITS[*]}"
    exit 0
else
    # 有失败
    echo "❌ 发现导致测试失败的 commit: $FAILED_COMMIT"
    echo ""
    echo "========== 失败 commit 信息 =========="
    echo "$ git show $FAILED_COMMIT --stat"
    git show "$FAILED_COMMIT" --stat 2>/dev/null || true
    echo "======================================"
    
    echo ""
    echo "========== 测试失败输出 =========="
    echo "$FAILED_OUTPUT"
    echo "==================================="
    
    # 回滚
    echo ""
    echo "🧹 [4/4] 回滚..."
    cleanup
    echo "✅ 已回滚"
    
    # 保存信息供 AI 分析
    {
        echo "=== FAILED COMMIT ==="
        echo "$FAILED_COMMIT"
        echo ""
        echo "=== COMMIT DIFF ==="
        git show "$FAILED_COMMIT" --no-color 2>/dev/null || true
        echo ""
        echo "=== TEST OUTPUT ==="
        echo "$FAILED_OUTPUT"
    } > /tmp/batch_cherrypick_fail.log
    
    echo ""
    echo "📋 已保存分析文件: /tmp/batch_cherrypick_fail.log"
    
    echo "TEST_FAIL"
    echo "PASSED:${PASSED_COMMITS[*]}"
    echo "FAILED:$FAILED_COMMIT"
    echo "OUTPUT:$FAILED_OUTPUT"
    
    exit 1
fi
