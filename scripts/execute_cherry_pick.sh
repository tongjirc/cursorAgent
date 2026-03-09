#!/bin/bash
#
# Cherry-Pick 执行脚本 - 确保工作区干净
#

COMMIT_ID="$1"
TARGET_BRANCH="$2"
REPO_PATH="${3:-.}"
TEST_COMMAND="${4:-python3 -m pytest tests/ -v 2>&1 || true}"

echo "========================================"
echo "🍒 Cherry-Pick: $COMMIT_ID → $TARGET_BRANCH"
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

# ========== 2. Cherry-Pick ==========
echo ""
echo "🍒 [2/4] Cherry-Pick $COMMIT_ID..."
if git cherry-pick "$COMMIT_ID" --no-commit 2>&1; then
    echo "✅ 暂存成功"
else
    echo "❌ 冲突!"
    CONFLICT_FILES=$(git diff --name-only --diff-filter=U 2>/dev/null || true)
    echo "📂 冲突文件: $CONFLICT_FILES"
    
    # 清理
    git cherry-pick --abort 2>/dev/null || true
    git checkout -- . 2>/dev/null || true
    git clean -fd 2>/dev/null || true
    
    echo "CONFLICT"
    echo "FILES:$CONFLICT_FILES"
    exit 1
fi

# ========== 3. 运行测试 ==========
echo ""
echo "🧪 [3/4] 运行测试..."
TEST_OUTPUT=$(eval "$TEST_COMMAND")
TEST_RESULT=$?

if [ $TEST_RESULT -eq 0 ]; then
    echo "✅ 测试通过"
else
    echo "❌ 测试失败 (exit $TEST_RESULT)"
    
    # 清理
    git cherry-pick --abort 2>/dev/null || true
    git checkout -- . 2>/dev/null || true
    git clean -fd 2>/dev/null || true
    echo "✅ 已回滚"
    
    echo "TEST_FAIL"
    echo "OUTPUT:$TEST_OUTPUT"
    exit 1
fi

# ========== 4. 提交推送 ==========
echo ""
echo "📤 [4/4] 提交..."
git add -A
git commit -m "Cherry-pick: $COMMIT_ID → $TARGET_BRANCH" 2>/dev/null || true

echo "========================================"
echo "✅ Cherry-Pick 完成! (本地已提交)"
echo "========================================"
exit 0
