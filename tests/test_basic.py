"""基本测试"""
import os

def test_env_configured():
    """检查环境变量配置"""
    from dotenv import load_dotenv
    load_dotenv()
    assert os.environ.get("REPO_PATH"), "REPO_PATH not set"

def test_scripts_exist():
    """检查脚本存在"""
    assert os.path.exists("scripts/execute_cherry_pick.sh")
