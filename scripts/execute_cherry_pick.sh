#!/bin/bash
#
# Cherry-Pick 执行脚本 - 确保工作区干净
#

COMMIT_ID="$1"
TARGET_BRANCH="$2"
REPO_PATH="${3:-.}"
TEST_COMMAND="${4:-python3 -m pytest tests/ -v 2>&1}"

echo "========================================"
echo "🍒 Cherry-Pick: $COMMIT_ID → $TARGET_BRANCH"
echo "========================================"

cd "$REPO_PATH"

ORIGINAL_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")

finish() {
    status=$1
    if [ -n "$ORIGINAL_BRANCH" ]; then
        echo ""
        echo "🔙 切回原分支: $ORIGINAL_BRANCH"
        git checkout "$ORIGINAL_BRANCH" 2>/dev/null || echo "⚠️ 无法切回 $ORIGINAL_BRANCH"
    fi
    exit "$status"
}

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
    finish 1
fi

# ========== 2. Cherry-Pick ==========
echo ""
echo "🍒 [2/4] Cherry-Pick $COMMIT_ID..."
if git cherry-pick "$COMMIT_ID" --no-commit 2>&1; then
    # 检查是否真的有变更（避免 already applied / 空 cherry-pick）
    if git diff --cached --quiet; then
        echo "ℹ️ 本次 Cherry-Pick 没有产生任何变更，可能该提交已在 $TARGET_BRANCH 上"
        echo "NO_CHANGE"
        finish 0
    fi

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
    finish 1
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
    finish 1
fi

# ========== 4. 提交推送 ==========
echo ""
echo "📤 [4/4] 提交..."
git add -A
git commit -m "Cherry-pick: $COMMIT_ID → $TARGET_BRANCH" 2>/dev/null || true

if git push origin "$TARGET_BRANCH" 2>/dev/null; then
    echo "✅ 推送成功"
else
    echo "⚠️ 推送失败 (本地OK)"
fi

echo "========================================"
echo "✅ 完成!"
echo "========================================"
echo ""
echo "📜 最近提交 (在 $TARGET_BRANCH 上):"
git --no-pager log "$TARGET_BRANCH" --oneline -5 2>/dev/null || git --no-pager log --oneline -5 2>/dev/null || true

finish 0
