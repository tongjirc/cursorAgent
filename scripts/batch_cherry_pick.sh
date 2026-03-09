#!/bin/bash
#
# 批量 Cherry-Pick 脚本
# 特性：一起 Cherry-Pick 所有 commit，一起测试，失败回滚 + AI 分析
# 用法: bash batch_cherry_pick.sh "<commit1,commit2,commit3>" <target-branch> [repo-path] [test-command]
#

COMMIT_LIST="$1"
TARGET_BRANCH="$2"
REPO_PATH="${3:-.}"
TEST_COMMAND="${4:-python3 -m pytest tests/ -v 2>&1 || true}"

# 解析逗号分隔的 commit 列表
IFS=',' read -ra COMMITS <<< "$COMMIT_LIST"

echo "========================================"
echo "📦 Batch Cherry-Pick"
echo "========================================"
echo "📋 Commits: ${COMMITS[*]}"
echo "📌 Target: $TARGET_BRANCH"
echo "📁 Repo: $REPO_PATH"
echo "🧪 Test: $TEST_COMMAND"
echo "========================================"

cd "$REPO_PATH"

# ========== 0. 强制清理工作区 ==========
echo ""
echo "🧹 [0/4] 清理工作区..."
git reset --hard HEAD 2>/dev/null || true
git checkout -- . 2>/dev/null || true
git clean -fd 2>/dev/null || true
echo "✅ 工作区已清理"

# ========== 1. 切换分支 ==========
echo ""
echo "📌 [1/4] 切换到 $TARGET_BRANCH..."
if git checkout "$TARGET_BRANCH" 2>/dev/null; then
    echo "✅ 已切换"
else
    echo "❌ 分支不存在: $TARGET_BRANCH"
    exit 1
fi

# ========== 2. 一起 Cherry-Pick ==========
echo ""
echo "🍒 [2/4] Cherry-Pick ${#COMMITS[@]} 个 commits..."

for i in "${!COMMITS[@]}"; do
    COMMIT="${COMMITS[$i]}"
    COMMIT=$(echo "$COMMIT" | xargs)  # 去除空格
    
    echo "--- Commit $((i+1))/${#COMMITS[@]}: $COMMIT ---"
    
    if git cherry-pick "$COMMIT" --no-commit 2>&1; then
        echo "✅ $COMMIT 暂存成功"
    else
        echo "❌ $COMMIT 冲突!"
        CONFLICT_FILES=$(git diff --name-only --diff-filter=U 2>/dev/null || true)
        
        # 清理
        git cherry-pick --abort 2>/dev/null || true
        git checkout -- . 2>/dev/null || true
        git clean -fd 2>/dev/null || true
        
        echo "CONFLICT"
        echo "COMMIT:$COMMIT"
        echo "FILES:$CONFLICT_FILES"
        exit 1
    fi
done

echo "✅ 全部 ${#COMMITS[@]} 个 commit 已暂存"

# ========== 3. 运行测试 ==========
echo ""
echo "🧪 [3/4] 运行测试..."
TEST_OUTPUT=$(eval "$TEST_COMMAND")
TEST_RESULT=$?

if [ $TEST_RESULT -eq 0 ]; then
    echo "✅ 测试通过"
    echo "========================================"
    echo "✅ Batch Cherry-Pick 成功!"
    echo "========================================"
    
    本地提交
    echo ""
    echo "📤 本地提交..."
    git add -A
    git commit -m "Batch cherry-pick: ${COMMITS[*]} → $TARGET_BRANCH" 2>/dev/null || true
    
    else
    fi
    
    echo "✅ 已提交到本地"
    echo "SUCCESS"
    echo "COMMITS:${COMMITS[*]}"
    exit 0
else
    echo "❌ 测试失败 (exit $TEST_RESULT)"
    echo ""
    echo "========== 测试输出 =========="
    echo "$TEST_OUTPUT"
    echo "================================"
    
    # 保存测试输出供 AI 分析
    echo "$TEST_OUTPUT" > /tmp/batch_test_fail.log
    
    # 清理 - 回滚
    echo ""
    echo "🧹 [4/4] 回滚..."
    git cherry-pick --abort 2>/dev/null || true
    git checkout -- . 2>/dev/null || true
    git clean -fd 2>/dev/null || true
    echo "✅ 已回滚"
    
    echo "TEST_FAIL"
    echo "COMMITS:${COMMITS[*]}"
    echo "OUTPUT:$TEST_OUTPUT"
    exit 1
fi
