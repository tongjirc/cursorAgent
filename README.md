# 🤖 Slack Cherry-Pick Bot

通过 Slack @Bot 触发 Git Cherry-Pick + 测试 + 推送

## 功能总览

| 命令 | 说明 | 失败处理 |
|------|------|----------|
| `!cherry-pick <commit> <branch>` | 单个 Cherry-Pick | 失败回滚 + AI 分析 |
| `!batch-cp <c1,c2,c3> <branch>` | 批量模式：一起 CP 一起测试 | 失败全部回滚 + AI 汇报 |
| `!step-cp <c1,c2,c3> <branch>` | 逐个模式：逐个 CP + 测试，失败继续下一个 | 失败回滚当前，继续下一个 |

---

## 🚀 快速开始

### 1. 安装依赖
```bash
cd ~/Documents/develop/cursorAgent
pip install -r requirements.txt
```

### 2. 配置环境变量
```bash
cp .env.example .env
# 编辑 .env 填入 Slack Tokens
```

### 3. 启动 Bot
```bash
python3 slack_listener.py
```

---

## 🔧 Slack Onboard 步骤

### 1. 创建 Slack App
打开 https://api.slack.com/apps → Create New App → From scratch
- App Name：`Local Cursor Trigger`（随便起）
- Development Slack Workspace：选你自己的 workspace

### 2. 开启 Socket Mode
- 左侧菜单 → Socket Mode → Enable Socket Mode（打开开关）
- 生成并复制 App-Level Token（`xapp-1-xxx`）

### 3. 配置权限（Scopes）
左侧菜单 → OAuth & Permissions → Bot Token Scopes 添加：
- `app_mentions:read`
- `chat:write`
- （可选）`im:read`、`im:write`、`mpim:read`、`mpim:write`

保存更改

### 4. 订阅事件
左侧菜单 → Event Subscriptions → Enable Events（打开开关）
- Subscribe to bot events → Add Bot User Event → 选择 `app_mention`
- 保存更改

### 5. 安装 App 到 Workspace
左侧菜单 → Install App → Install to Workspace → 同意授权
- 复制 Bot User OAuth Token（`xoxb-xxx`）

### 6. 获取 Signing Secret（可选但推荐）
左侧菜单 → Basic Information → App Credentials → Signing Secret → Show → 复制

### 7. 配置 .env
```bash
SLACK_BOT_TOKEN=xoxb-xxx
SLACK_APP_TOKEN=xapp-xxx
SLACK_SIGNING_SECRET=xxx
REPO_PATH=/Users/alvinchen/Documents/develop/cursorAgent
```

---

## 📖 使用方法

### 单个 Cherry-Pick
```
@Bot !cherry-pick abc1234 main
```

### Batch 模式（一起 CP 一起测试）
```
@Bot !batch-cp abc123,def456,ghi789 main
```
- 一次性 Cherry-Pick 所有 commits
- 一起运行测试
- 失败 → **全部回滚** + AI 汇报

### Step 模式（逐个 CP 逐个测试）
```
@Bot !step-cp abc123,def456,ghi789 main
```
- 逐个 Cherry-Pick + 测试
- 失败/冲突 → 回滚当前 commit，继续下一个
- 最终汇报：哪些通过、哪些失败、哪些冲突

---

## 🔄 自动处理流程

```
用户发送命令
        ↓
    解析命令 (commit + branch)
        ↓
┌─────────────────────────────────────┐
│  自动处理流程                         │
│  1. 清理工作区                      │
│  2. 切换到 target_branch           │
│  3. Cherry-Pick commit(s)          │
│  4. 运行测试 (pytest)               │
│  5. 判定结果                        │
└─────────────────────────────────────┘
        ↓
    结果处理
    ↙       ↘
  成功        失败/冲突
    ↓          ↓
 ✅ 提交推送  🔄 自动回滚
              ↓
         切回原分支
```

### 详细流程

1. **清理工作区**
   ```bash
   git reset --hard HEAD
   git checkout -- .
   git clean -fd
   ```

2. **切换到目标分支**
   ```bash
   git checkout <target_branch>
   ```

3. **执行 Cherry-Pick**
   - 单个：`git cherry-pick <commit> --no-commit`
   - Batch：逐个 cherry-pick 所有 commits
   - Step：逐个 cherry-pick + 测试

4. **运行测试**
   ```bash
   python3 -m pytest tests/ -v
   ```

5. **结果处理**
   - **成功**：提交并推送
   - **失败/冲突**：自动回滚到测试前状态

6. **切回原分支**
   ```bash
   git checkout <original_branch>
   ```

---

## 📁 文件结构

```
cursorAgent/
├── slack_listener.py              # Bot 主程序
├── scripts/
│   ├── execute_cherry_pick.sh    # 单个 Cherry-Pick
│   ├── batch_cherry_pick.sh      # 批量模式
│   └── step_cherry_pick.sh       # 逐个模式
├── tests/
│   ├── test_pass.py              # 通过的测试
│   └── test_fail.py              # 失败的测试
├── .env                          # Token 配置
├── .cursor-agent-rules.json      # Agent 规则
└── .cursor-git-config.json       # Git 权限配置
```

### 测试场景汇总

| 场景 | 命令 | 预期结果 |
|------|------|----------|
| 单个 CP 成功 | `!cherry-pick <commit> main` | 成功提交推送 |
| 单个 CP 冲突 | `!cherry-pick <conflict_commit> main` | 回滚，报告冲突 |
| 单个 CP 测试失败 | `!cherry-pick <fail_commit> main` | 回滚，AI 分析 |
| Batch 全部成功 | `!batch-cp <c1>,<c2> main` | 全部提交推送 |
| Batch 失败 | `!batch-cp <c1>,<c2> main` | 全部回滚，AI 汇报 |
| Step 全部成功 | `!step-cp <c1>,<c2> main` | 逐个通过，最终成功 |
| Step 部分失败 | `!step-cp <ok>,<fail> main` | ok通过，fail回滚，继续 |
| Step 冲突 | `!step-cp <ok>,<conflict> main` | ok通过，conflict回滚，继续 |

## ⚙️ 配置

### 环境变量 (.env)

```bash
SLACK_BOT_TOKEN=xoxb-xxx
SLACK_APP_TOKEN=xapp-xxx
SLACK_SIGNING_SECRET=xxx
REPO_PATH=/Users/alvinchen/Documents/develop/cursorAgent
```

### 测试命令自定义

在脚本中可以指定测试命令：
```bash
bash scripts/execute_cherry_pick.sh <commit> <branch> <repo> "npm test"
bash scripts/batch_cherry_pick.sh "<c1,c2>" <branch> <repo> "make test"
bash scripts/step_cherry_pick.sh "<c1,c2>" <branch> <repo> "python -m pytest"
```

---

## 🔧 故障排查

### Bot 没响应
- 检查 Bot 是否运行：`ps aux | grep slack_listener`
- 检查 Slack Token 是否正确

### 权限不足
- 确认已添加 `app_mentions:read` 和 `chat:write` scopes

### 测试失败
- 手动运行测试：`python3 -m pytest tests/ -v`

---

## 📝 更新日志

- 2026-03-09: 添加 Batch CP 和 Step CP 模式
- 2026-03-08: 初始版本
