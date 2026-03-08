# 🤖 Slack Cherry-Pick Bot

通过 Slack @Bot 触发 Git Cherry-Pick + 测试 + 推送

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

## 配置

### Slack API 需要
- Bot Token (`xoxb-xxx`)
- App-Level Token (`xapp-xxx`)
- Signing Secret

详见: https://api.slack.com/

## 使用

在 Slack 发送:
```
@Bot !cherry-pick <commit-id> <branch>
```

## 文件结构
```
├── slack_listener.py      # Bot 主程序
├── scripts/
│   └── execute_cherry_pick.sh  # Cherry-Pick 脚本
├── .env                  # Token 配置 (不上传)
└── .cursor-agent-rules.json  # Agent 规则
```

## 测试
```bash
python3 -m pytest
```
