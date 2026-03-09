"""
Cherry-Pick 系统流程测试
测试不接入 Slack 的 Cherry-Pick 流程
"""
import subprocess
import pytest

REPO = "/Users/alvinchen/Documents/develop/cursorAgent"
OK_COMMIT = "217cd71"  # test_ok.py - passes
FAIL_COMMIT = "58721ef"  # test_fail.py - fails

def run_script(cmd, timeout=60):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=REPO)
    return result.returncode, result.stdout, result.stderr

def test_single_cp_success():
    """测试1: 单个 Cherry-Pick 成功"""
    code, out, err = run_script(
        f'bash scripts/execute_cherry_pick.sh {OK_COMMIT} main . "python3 -m pytest tests/test_ok.py -v"'
    )
    assert "完成" in out or code == 0, f"Expected success: {out}"

def test_single_cp_fail():
    """测试2: 单个 Cherry-Pick 测试失败"""
    code, out, err = run_script(
        f'bash scripts/execute_cherry_pick.sh {FAIL_COMMIT} main . "python3 -m pytest tests/test_fail.py -v"'
    )
    assert "TEST_FAIL" in out, f"Expected test fail: {out}"

def test_batch_cp_success():
    """测试3: Batch Cherry-Pick 全部成功"""
    code, out, err = run_script(
        f'bash scripts/batch_cherry_pick.sh "{OK_COMMIT},{OK_COMMIT}" main . "python3 -m pytest tests/test_ok.py -v"'
    )
    assert "SUCCESS" in out, f"Expected success: {out}"

def test_batch_cp_fail():
    """测试4: Batch Cherry-Pick 有失败"""
    code, out, err = run_script(
        f'bash scripts/batch_cherry_pick.sh "{OK_COMMIT},{FAIL_COMMIT}" main . "python3 -m pytest tests/ -v"'
    )
    assert "TEST_FAIL" in out or code != 0, f"Expected fail: {out}"

def test_step_cp_success():
    """测试5: Step Cherry-Pick 全部成功"""
    code, out, err = run_script(
        f'bash scripts/step_cherry_pick.sh "{OK_COMMIT},{OK_COMMIT}" main . "python3 -m pytest tests/test_ok.py -v"'
    )
    assert "STEP_SUCCESS" in out, f"Expected success: {out}"

def test_step_cp_partial():
    """测试6: Step Cherry-Pick 部分失败"""
    code, out, err = run_script(
        f'bash scripts/step_cherry_pick.sh "{OK_COMMIT},{FAIL_COMMIT}" main . "python3 -m pytest tests/ -v"'
    )
    assert "通过" in out and "失败" in out, f"Expected partial: {out}"
