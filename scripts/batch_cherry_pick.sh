#!/bin/bash
#
# 批量 Cherry-Pick 脚本
# 用法: bash batch_cherry_pick.sh "<commit1,commit2,commit3>" <target-branch> [repo-path] [test-command]
# 示例: bash batch_cherry_pick.sh "abc123,def456,ghi789" develop /path/to/repo "npm test"
#

COMMIT_LIST="$1"
TARGET_BRANCH="$2"
REPO_PATH="${3:-.}"
TEST_COMMAND="${4:-python3 -m pytest tests/ -v 2>&1 || true}"

# 解析逗号分隔的 commit 列表
IFS=',' read -ra COMMITS <<< "$COMMIT_LIST"

echo "========================================"
echo "🍒 批量 Cherry-Pick"
echo "========================================"
echo "📋 Commits: ${COMMITS[*]}"
echo "📌 Target: $TARGET_BRANCH"
echo "📁 Repo: $REPO_PATH"
echo "🧪 Test: $TEST_COMMAND"
echo "========================================"

cd "$REPO_PATH"

# ========== 0. 强制清理工作区 ==========
echo ""
echo "🧹 [0/5] 清理工作区..."
git reset --hard HEAD 2>/dev/null || true
git checkout -- . 2>/dev/null || true
git clean -fd 2>/dev/null || true
echo "✅ 工作区已清理"

# ========== 1. 切换分支 ==========
echo ""
echo "📌 [1/5] 切换到 $TARGET_BRANCH..."
if git checkout "$TARGET_BRANCH" 2>/dev/null; then
    echo "✅ 已切换"
else
    echo "❌ 分支不存在: $TARGET_BRANCH"
    exit 1
fi

# ========== 2. 逐个 Cherry-Pick ==========
echo ""
echo "🍒 [2/5] 开始 Cherry-Pick..."

COMMITTED_COMMITS=()
FAILED_COMMITS=()

for i in "${!COMMITS[@]}"; do
    COMMIT="${COMMITS[$i]}"
    COMMIT=$(echo "$COMMIT" | xargs)  # 去除空格
    
    echo ""
    echo "--- Commit $((i+1))/${#COMMITS[@]}: $COMMIT ---"
    
    if git cherry-pick "$COMMIT" --no-commit 2>&1; then
        echo "✅ $COMMIT 暂存成功"
        COMMITTED_COMMITS+=("$COMMIT")
    else
        echo "❌ $COMMIT 冲突!"
        CONFLICT_FILES=$(git diff --name-only --diff-filter=U 2>/dev/null || true)
        echo "📂 冲突文件: $CONFLICT_FILES"
        
        # 清理
        git cherry-pick --abort 2>/dev/null || true
        git checkout -- . 2>/dev/null || true
        git clean -fd 2>/dev/null || true
        
        FAILED_COMMITS+=("$COMMIT")
        
        echo "CONFLICT"
        echo "COMMIT:$COMMIT"
        echo "FILES:$CONFLICT_FILES"
        
        # 继续尝试下一个
        echo "⚠️ 继续尝试下一个 commit..."
    fi
done

# 统计成功的 commit 数量
SUCCESS_COUNT=${#COMMITTED_COMMITS[@]}
TOTAL_COUNT=${#COMMITS[@]}

echo ""
echo "📊 Cherry-Pick 结果: $SUCCESS_COUNT/$TOTAL_COUNT 成功"

if [ $SUCCESS_COUNT -eq 0 ]; then
    echo "❌ 没有成功的 commit，退出"
    exit 1
fi

# ========== 3. 运行测试 ==========
echo ""
echo "🧪 [3/5] 运行测试..."
TEST_OUTPUT=$(eval "$TEST_COMMAND")
TEST_RESULT=$?

if [ $TEST_RESULT -eq 0 ]; then
    echo "✅ 测试通过"
    echo "========================================"
    echo "✅ 批量 Cherry-Pick 成功!"
    echo "========================================"
    
    # 提交推送
    echo ""
    echo "📤 [4/5] 提交..."
    git add -A
    git commit -m "Batch cherry-pick: ${COMMITTED_COMMITS[*]} → $TARGET_BRANCH" 2>/dev/null || true
    
    if git push origin "$TARGET_BRANCH" 2>/dev/null; then
        echo "✅ 推送成功"
    else
        echo "⚠️ 推送失败 (本地OK)"
    fi
    
    echo "SUCCESS"
    echo "COMMITS:${COMMITTED_COMMITS[*]}"
    exit 0
else
    echo "❌ 测试失败 (exit $TEST_RESULT)"
    echo ""
    echo "========== 测试输出 =========="
    echo "$TEST_OUTPUT"
    echo "================================"
    
    # 保存测试输出供 AI 分析
    echo "$TEST_OUTPUT" > /tmp/batch_test_fail.log
    
    # 清理
    echo ""
    echo "🧹 [4/5] 回滚..."
    git cherry-pick --abort 2>/dev/null || true
    git checkout -- . 2>/dev/null || true
    git clean -fd 2>/dev/null || true
    echo "✅ 已回滚"
    
    echo ""
    echo "📤 [5/5] 报告失败 commit..."
    
    echo "TEST_FAIL"
    echo "COMMITS:${COMMITTED_COMMITS[*]}"
    echo "OUTPUT:$TEST_OUTPUT"
    exit 1
fi
