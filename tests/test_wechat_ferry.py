"""WeChatFerry 测试 - 运行: python -m tests.test_wechat_ferry"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def main():
    print("=" * 55)
    print("  WeChatFerry 个人微信推送测试")
    print("=" * 55)
    try:
        from push.wechat_ferry_notify import wechat_ferry_notify
        ok = wechat_ferry_notify("【量化测试】WeChatFerry 推送测试")
        print("[OK]" if ok else "[FAIL]")
    except Exception as e:
        print(f"[SKIP] {e}")
    sys.exit(0)

if __name__ == "__main__":
    main()
