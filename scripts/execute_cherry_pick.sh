#!/bin/bash
#
# Cherry-Pick 执行脚本 - 带清理逻辑
#

set -e

COMMIT_ID="$1"
TARGET_BRANCH="$2"
REPO_PATH="${3:-.}"
TEST_COMMAND="${4:-python3 -m pytest tests/ -v}"

echo "========================================"
echo "🍒 Cherry-Pick 任务"
echo "========================================"

cd "$REPO_PATH"

# ========== 清理: 确保干净的工作区 ==========
echo ""
echo "🧹 [0/5] 清理工作区..."
git status --short | grep -q . && git checkout -- . || true
git clean -fd 2>/dev/null || true
echo "✅ 工作区已清理"

# ========== 步骤 1: 切换分支 ==========
echo ""
echo "📌 [1/5] 切换到目标分支: $TARGET_BRANCH"
if git checkout "$TARGET_BRANCH" 2>/dev/null; then
    echo "✅ 分支切换成功"
else
    echo "❌ 分支不存在: $TARGET_BRANCH"
    exit 1
fi

# ========== 步骤 2: Cherry-Pick ==========
echo ""
echo "🍒 [2/5] Cherry-Pick: $COMMIT_ID"
if git cherry-pick "$COMMIT_ID" --no-commit; then
    echo "✅ Cherry-Pick 暂存成功"
else
    echo "❌ Cherry-Pick 失败 - 存在冲突"
    CONFLICT_FILES=$(git diff --name-only --diff-filter=U)
    echo "📂 冲突文件: $CONFLICT_FILES"
    
    # 获取冲突详情用于 AI 分析
    CONFLICT_CONTENT=$(git diff --diff-filter=U 2>/dev/null || echo "")
    
    # 清理
    git cherry-pick --abort 2>/dev/null || true
    git checkout -- . 2>/dev/null || true
    git clean -fd 2>/dev/null || true
    echo "✅ 已回滚工作区"
    
    # 输出冲突信息供 AI 分析
    echo "CONFLICT_DETECTED"
    echo "FILES:$CONFLICT_FILES"
    echo "DETAILS:$CONFLICT_CONTENT"
    exit 1
fi

# ========== 步骤 3: 运行测试 ==========
echo ""
echo "🧪 [3/5] 运行测试: $TEST_COMMAND"
if eval "$TEST_COMMAND"; then
    echo "✅ 测试通过"
else
    TEST_EXIT_CODE=$?
    TEST_OUTPUT=$(eval "$TEST_COMMAND" 2>&1 || true)
    echo "❌ 测试失败 (exit code: $TEST_EXIT_CODE)"
    
    # 清理
    git cherry-pick --abort 2>/dev/null || true
    git checkout -- . 2>/dev/null || true
    git clean -fd 2>/dev/null || true
    echo "✅ 已回滚工作区"
    
    echo "TEST_FAILED:$TEST_OUTPUT"
    exit 1
fi

# ========== 步骤 4: 提交并推送 ==========
echo ""
echo "📤 [4/5] 提交并推送..."
git add -A
git commit -m "Cherry-pick: $COMMIT_ID → $TARGET_BRANCH"

# 尝试推送
if git push origin "$TARGET_BRANCH" 2>/dev/null; then
    echo "✅ 推送成功"
else
    echo "⚠️ 推送失败 (可能没有 remote)，本地提交成功"
    echo "✅ 已提交到本地"
fi

echo ""
echo "========================================"
echo "✅ 任务完成!"
echo "========================================"
exit 0
