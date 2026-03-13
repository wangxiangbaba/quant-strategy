"""推送测试 - 运行: python -m tests.test_push"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def main():
    print("=" * 50)
    print("推送诊断 (飞书 / Telegram / 企业微信)")
    print("=" * 50)
    try:
        from push import push
        push("【测试】push 推送测试")
        print("已调用 push()，请检查各渠道")
    except Exception as e:
        print(f"异常: {e}")
    sys.exit(0)

if __name__ == "__main__":
    main()
