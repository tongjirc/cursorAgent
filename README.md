# 🤖 Slack Cherry-Pick Bot

通过 Slack @Bot 触发 Git Cherry-Pick + 测试 + 推送

## 功能

- **Slack Socket Mode** - 通过 `@Bot` 命令接收 Cherry-Pick 请求
- **自动测试** - Cherry-Pick 后自动运行测试
- **失败回滚** - 测试失败自动回滚
- **批量模式** - 支持批量/逐个 Cherry-Pick

## 快速开始

### 1. 安装依赖
```bash
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

## Slack 命令

| 命令 | 说明 |
|------|------|
| `@Bot cherry-pick <commit> <branch>` | 单个 Cherry-Pick |
| `@Bot batch-cp <c1,c2,c3> <branch>` | 批量 Cherry-Pick (一起测试) |
| `@Bot step-cp <c1,c2,c3> <branch>` | 逐个 Cherry-Pick (单独测试) |

## Scripts

### execute_cherry_pick.sh

**单个 Cherry-Pick 脚本**

```bash
bash execute_cherry_pick.sh <commit> <target-branch> [repo-path] [test-command]
```

**流程**:
1. 清理工作区
2. 切换到目标分支
3. Cherry-Pick 单个 commit
4. 运行测试
5. 成功 → 提交推送；失败 → 回滚

**返回码**:
- `0` = 成功
- `1` = 测试失败 (已回滚)
- `2` = 冲突

---

### batch_cherry_pick.sh

**批量 Cherry-Pick 脚本**

```bash
bash batch_cherry_pick.sh "c1,c2,c3" <target-branch> [repo-path] [test-command]
```

**特性**:
- 所有 commits 一起 Cherry-Pick
- 一起运行测试
- **失败 → 全部回滚**

**流程**:
1. 清理工作区
2. 切换到目标分支
3. 依次 Cherry-Pick 所有 commits (暂存)
4. 运行测试 (全部一起)
5. 成功 → 提交推送；失败 → 全部回滚

---

### step_cherry_pick.sh

**逐个 Cherry-Pick 脚本**

```bash
bash step_cherry_pick.sh "c1,c2,c3" <target-branch> [repo-path] [test-command]
```

**特性**:
- 逐个 Cherry-Pick
- 每个单独测试
- 失败 → 回滚当前，继续下一个
- 最终汇报：哪些成功、哪些失败

**流程**:
1. 清理工作区
2. 切换到目标分支
3. 循环每个 commit:
   - Cherry-Pick
   - 运行测试
   - 成功 → 暂存
   - 失败 → 回滚，继续下一个
4. 暂存成功的 commits
5. 提交推送

---

### smart_batch_cherry_pick.sh

**智能批量 Cherry-Pick 脚本**

```bash
bash smart_batch_cherry_pick.sh "c1,c2,c3" <target-branch> [repo-path] [test-command]
```

**特性**:
- 类似 batch，但失败后**二分查找定位问题 commit**
- 适用于批量 commits 不知道哪个有问题的场景

---

## UT 测试

```bash
python3 -m pytest tests/test_cherry_pick_flow.py -v
```

### 测试文件

| 文件 | 说明 |
|------|------|
| `tests/test_ok.py` | 会通过的测试 |
| `tests/test_fail.py` | 会失败的测试 |
| `tests/test_cherry_pick_flow.py` | 流程测试 (6个测试用例) |

### 测试 Commits

- **OK**: `217cd71` - 添加 test_ok.py
- **FAIL**: `58721ef` - 添加 test_fail.py

---

## 目录结构

```
cursorAgent/
├── slack_listener.py           # Slack Bot 主程序
├── scripts/
│   ├── execute_cherry_pick.sh  # 单个 Cherry-Pick
│   ├── batch_cherry_pick.sh    # 批量 Cherry-Pick
│   ├── step_cherry_pick.sh     # 逐个 Cherry-Pick
│   └── smart_batch_cherry_pick.sh  # 智能批量
├── tests/
│   ├── test_ok.py
│   ├── test_fail.py
│   └── test_cherry_pick_flow.py
├── .env                        # Slack Tokens (敏感)
├── .env.example                # 环境变量模板
├── requirements.txt            # Python 依赖
└── README.md
```

## 环境变量 (.env)

```bash
SLACK_BOT_TOKEN=xoxb-xxx
SLACK_APP_TOKEN=xapp-xxx
SLACK_SIGNING_SECRET=xxx
REPO_PATH=/path/to/repo
```

---

## License

MIT
