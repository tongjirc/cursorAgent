#!/usr/bin/env python3
"""
Slack Socket Mode Listener - 带队列和 AI 分析 + 用户确认
"""

import os
import sys
import subprocess
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
pending_confirm = {}  # 存储待确认的任务

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

def check_agent_login():
    """检查 agent 是否登录"""
    try:
        result = subprocess.run(
            ["agent", "status"],
            capture_output=True,
            text=True,
            timeout=10
        )
        return "logged in" in result.stdout.lower() or "authenticated" in result.stdout.lower()
    except:
        return False

def analyze_with_ai(prompt):
    """使用 AI 分析并返回建议"""
    # 检查登录状态
    if not check_agent_login():
        print("⚠️ Agent 未登录，跳过 AI 分析")
        return None
    
    try:
        result = subprocess.run(
            ["agent", "-p", "-f", prompt],
            capture_output=True,
            text=True,
            timeout=90,
            cwd=REPO_PATH
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout
    except Exception as e:
        print(f"❌ AI 分析失败: {e}")
    return None

def analyze_conflict(conflict_files, conflict_details):
    """分析冲突"""
    prompt = f"""分析以下 Git 冲突，给出具体解决建议:

冲突文件: {conflict_files}

请简洁回答:
1. 冲突原因 (1句话)
2. 推荐解决方案 (2-3步)"""
    
    return analyze_with_ai(prompt)

def analyze_test_failure(test_output):
    """分析测试失败"""
    prompt = f"""分析以下测试失败，给出修复建议:

测试输出:
{test_output[:2000]}

请简洁回答:
1. 失败原因2.  (1句话)
修复步骤 (2-3步)"""
    
    return analyze_with_ai(prompt)

def run_cherry_pick(commit_id, target_branch):
    """执行 Cherry-Pick"""
    script_path = os.path.join(REPO_PATH, "scripts", "execute_cherry_pick.sh")
    
    cmd = ["bash", script_path, commit_id, target_branch, REPO_PATH, "python3 -m pytest tests/ -v 2>&1 || true"]
    
    print(f"🔧 执行: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=REPO_PATH,
            shell=True
        )
        
        output = result.stdout + "\n" + result.stderr
        
        return {
            "success": result.returncode == 0,
            "output": output,
            "returncode": result.returncode,
            "is_conflict": "CONFLICT" in output or "conflict" in output.lower(),
            "is_test_fail": "FAILED" in output or "fail" in output.lower()
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
    """处理队列"""
    global processing, pending_confirm
    
    while True:
        if processing or task_queue.empty():
            break
        
        processing = True
        task = task_queue.get()
        
        commit_id = task["commit_id"]
        target_branch = task["target_branch"]
        say = task["say"]
        ts = task["ts"]
        
        say(f"🔄 开始 Cherry-Pick: `{commit_id}` → `{target_branch}`", thread_ts=ts)
        
        result = run_cherry_pick(commit_id, target_branch)
        
        if result["success"]:
            say(f"✅ **Cherry-Pick 成功!**\n\n```\n{result['output'][-500:]}\n```", thread_ts=ts)
        
        elif result["is_conflict"]:
            conflict_files = "未知"
            for line in result["output"].split("\n"):
                if ".js" in line or ".py" in line:
                    conflict_files = line.strip()
            
            say("⚠️ **Cherry-Pick 失败 - 冲突**", thread_ts=ts)
            
            # AI 分析
            ai_suggestion = analyze_conflict(conflict_files, result["output"])
            
            if ai_suggestion:
                # 存储待确认
                pending_confirm[ts] = {
                    "type": "conflict",
                    "commit_id": commit_id,
                    "target_branch": target_branch,
                    "suggestion": ai_suggestion
                }
                
                say(f"🤖 **AI 建议:**\n\n{ai_suggestion[:1000]}\n\n"
                    f"是否接受建议并继续? 回复 `yes` 或 `no`", 
                    thread_ts=ts)
            else:
                say(f"📂 **冲突文件:** `{conflict_files}`\n\n"
                    f"请手动解决后重试", thread_ts=ts)
        
        elif result["is_test_fail"]:
            say("⚠️ **Cherry-Pick 失败 - 测试失败**", thread_ts=ts)
            
            ai_suggestion = analyze_test_failure(result["output"])
            
            if ai_suggestion:
                pending_confirm[ts] = {
                    "type": "test_fail",
                    "commit_id": commit_id,
                    "target_branch": target_branch,
                    "suggestion": ai_suggestion
                }
                
                say(f"🤖 **AI 建议:**\n\n{ai_suggestion[:1000]}\n\n"
                    f"是否接受建议并继续? 回复 `yes` 或 `no`",
                    thread_ts=ts)
            else:
                say(f"🧪 **测试失败**\n\n```\n{result['output'][-500:]}\n```", 
                    thread_ts=ts)
        
        else:
            say(f"❌ **失败:**\n\n```\n{result['output'][-500:]}\n```", thread_ts=ts)
        
        processing = False

@app.event("app_mention")
def handle_mention(event, say, logger):
    global BOT_ID, processing, pending_confirm
    
    if not BOT_ID:
        get_bot_id()
    
    user = event.get("user")
    text = event.get("text")
    ts = event.get("ts")
    
    print(f"📩 收到消息 from {user}: {text}")
    
    # 提取命令
    clean_text = text
    if BOT_ID:
        clean_text = text.replace(f"<@{BOT_ID}>", "").strip()
    
    # 处理确认回复
    if ts in pending_confirm:
        confirm = clean_text.lower().strip()
        if confirm == "yes" or confirm == "y":
            info = pending_confirm[ts]
            say(f"✅ 好的，正在应用建议并重试...", thread_ts=ts)
            # TODO: 实现自动应用建议
            del pending_confirm[ts]
        elif confirm == "no" or confirm == "n":
            say("❌ 已取消", thread_ts=ts)
            del pending_confirm[ts]
        return
    
    # Cherry-Pick 命令
    if "!cherry-pick" in clean_text:
        parts = clean_text.split()
        if len(parts) >= 3:
            commit_id = parts[1]
            target_branch = parts[2]
            
            task_queue.put({
                "commit_id": commit_id,
                "target_branch": target_branch,
                "say": say,
                "ts": ts
            })
            
            queue_size = task_queue.qsize()
            if queue_size > 1:
                say(f"⏳ 队列中 (前{mqueue_size-1}个任务)", thread_ts=ts)
            
            if not processing:
                process_queue()
        else:
            say("❌ 格式: `!cherry-pick <commit> <branch>`", thread_ts=ts)
    else:
        say(f"🤖 支持: `!cherry-pick <commit> <branch>`", thread_ts=ts)

if __name__ == "__main__":
    if not SLACK_BOT_TOKEN or not SLACK_APP_TOKEN:
        print("❌ 缺少 Token")
        sys.exit(1)
    
    bot_id = get_bot_id()
    
    print("="*50)
    print("🚀 Slack Bot 启动")
    print(f"   Bot ID: {bot_id}")
    print(f"   Agent: {'已登录' if check_agent_login() else '未登录'}")
    print("="*50)
    
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()
