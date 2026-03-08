#!/usr/bin/env python3
"""
Slack Socket Mode Listener - 带队列和 AI 分析
"""

import os
import sys
import subprocess
import threading
from queue import Queue
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN", "")
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")
REPO_PATH = os.environ.get("REPO_PATH", "/Users/alvinchen/Documents/develop/cursorAgent")

# 任务队列
task_queue = Queue()
processing = False

app = App(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)

BOT_ID = None

def get_bot_id():
    global BOT_ID
    if BOT_ID:
        return BOT_ID
    try:
        resp = app.client.auth_test()
        if resp["ok"]:
            BOT_ID = resp["user_id"]
            return BOT_ID
    except Exception as e:
        print(f"❌ 获取 Bot ID 失败: {e}")
    return None

def analyze_conflict_with_ai(conflict_files, conflict_details):
    """使用 AI 分析冲突并给出建议"""
    prompt = f"""分析以下 Git 冲突并给出具体的解决建议:

冲突文件: {conflict_files}

冲突详情:
{conflict_details[:2000]}

请给出:
1. 冲突原因分析
2. 具体的解决步骤
3. 代码级别的建议"""

    try:
        result = subprocess.run(
            ["agent", "-p", "-f", prompt],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=REPO_PATH
        )
        return result.stdout if result.returncode == 0 else None
    except Exception as e:
        return None

def analyze_test_failure_with_ai(test_output):
    """使用 AI 分析测试失败并给出建议"""
    prompt = f"""分析以下测试失败并给出修复建议:

测试输出:
{test_output[:3000]}

请给出:
1. 失败原因分析
2. 具体的修复步骤
3. 需要修改的文件和建议"""

    try:
        result = subprocess.run(
            ["agent", "-p", "-f", prompt],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=REPO_PATH
        )
        return result.stdout if result.returncode == 0 else None
    except Exception as e:
        return None

def run_cherry_pick(commit_id, target_branch):
    """执行 Cherry-Pick"""
    script_path = os.path.join(REPO_PATH, "scripts", "execute_cherry_pick.sh")
    
    cmd = ["bash", script_path, commit_id, target_branch, REPO_PATH, "python3 -m pytest tests/ -v"]
    
    print(f"🔧 执行命令: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=REPO_PATH
        )
        
        output = result.stdout + "\n" + result.stderr
        
        return {
            "success": result.returncode == 0,
            "output": output,
            "returncode": result.returncode,
            "is_conflict": "CONFLICT_DETECTED" in output,
            "is_test_fail": "TEST_FAILED:" in output
        }
    except Exception as e:
        return {
            "success": False,
            "output": str(e),
            "returncode": -1,
            "is_conflict": False,
            "is_test_fail": False
        }

def process_queue():
    """处理队列中的任务"""
    global processing
    
    while True:
        if processing or task_queue.empty():
            break
        
        processing = True
        task = task_queue.get()
        
        commit_id = task["commit_id"]
        target_branch = task["target_branch"]
        say = task["say"]
        ts = task["ts"]
        
        say(f"🔄 开始执行 Cherry-Pick: `{commit_id}` → `{target_branch}`", thread_ts=ts)
        
        result = run_cherry_pick(commit_id, target_branch)
        
        if result["success"]:
            say(f"✅ **Cherry-Pick 成功!**\n\n"
                f"Commit: `{commit_id}` → `{target_branch}`\n\n"
                f"```\n{result['output'][-500:]}\n```", 
                thread_ts=ts)
        else:
            output = result["output"]
            
            if result["is_conflict"]:
                # 提取冲突信息
                conflict_files = ""
                conflict_details = ""
                for line in output.split("\n"):
                    if line.startswith("FILES:"):
                        conflict_files = line.replace("FILES:", "").strip()
                    if line.startswith("DETAILS:"):
                        conflict_details = line.replace("DETAILS:", "").strip()
                
                say("⚠️ **Cherry-Pick 失败 - 冲突**", thread_ts=ts)
                
                # AI 分析冲突
                ai_suggestion = analyze_conflict_with_ai(conflict_files, conflict_details)
                
                if ai_suggestion:
                    say(f"🤖 **AI 建议:**\n\n{ai_suggestion[:1500]}", thread_ts=ts)
                else:
                    say(f"📂 **冲突文件:** `{conflict_files}`\n\n"
                        f"请手动解决后执行:\n"
                        f"1. `git add <file>`\n"
                        f"2. `git cherry-pick --continue`\n"
                        f"或放弃: `git cherry-pick --abort`", 
                        thread_ts=ts)
                        
            elif result["is_test_fail"]:
                test_output = ""
                for line in output.split("\n"):
                    if line.startswith("TEST_FAILED:"):
                        test_output = line.replace("TEST_FAILED:", "").strip()
                
                say("⚠️ **Cherry-Pick 失败 - 测试失败**", thread_ts=ts)
                
                # AI 分析测试失败
                ai_suggestion = analyze_test_failure_with_ai(test_output)
                
                if ai_suggestion:
                    say(f"🤖 **AI 建议:**\n\n{ai_suggestion[:1500]}", thread_ts=ts)
                else:
                    say(f"🧪 **测试失败**\n\n```\n{test_output[-800:]}\n```\n\n"
                        f"请修复后重试", 
                        thread_ts=ts)
            else:
                say(f"❌ **Cherry-Pick 失败!**\n\n```\n{output[-500:]}\n```", thread_ts=ts)
        
        processing = False

@app.event("app_mention")
def handle_mention(event, say, logger):
    global BOT_ID, processing
    
    if not BOT_ID:
        get_bot_id()
    
    user = event.get("user")
    text = event.get("text")
    channel = event.get("channel")
    ts = event.get("ts")
    
    print(f"📩 收到消息 from {user}: {text}")
    
    # 提取命令
    clean_text = text
    if BOT_ID:
        clean_text = text.replace(f"<@{BOT_ID}>", "").strip()
    
    if "!cherry-pick" in clean_text:
        parts = clean_text.split()
        if len(parts) >= 3:
            commit_id = parts[1]
            target_branch = parts[2]
            
            # 加入队列
            task_queue.put({
                "commit_id": commit_id,
                "target_branch": target_branch,
                "say": say,
                "ts": ts
            })
            
            queue_size = task_queue.qsize()
            if queue_size > 1:
                say(f"⏳ 已加入队列 (前方还有 {queue_size-1} 个任务)，请稍候...", thread_ts=ts)
            else:
                say(f"🔄 收到请求: `{commit_id}` → `{target_branch}`", thread_ts=ts)
            
            # 启动处理
            if not processing:
                process_queue()
        else:
            say("❌ 格式错误!\n\n`!cherry-pick <commit-id> <branch>`", thread_ts=ts)
    else:
        say(f"🤖 收到: `{clean_text}`\n\n`!cherry-pick <commit> <branch>`", thread_ts=ts)

if __name__ == "__main__":
    if not SLACK_BOT_TOKEN or not SLACK_APP_TOKEN:
        print("❌ 缺少 Token")
        sys.exit(1)
    
    bot_id = get_bot_id()
    
    print("="*50)
    print("🚀 Slack Bot 启动 (带队列 + AI 分析)")
    print(f"   Bot ID: {bot_id}")
    print(f"   仓库: {REPO_PATH}")
    print("="*50)
    
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()
