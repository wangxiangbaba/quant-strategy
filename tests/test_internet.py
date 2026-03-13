"""网络连接测试 - 运行: python -m tests.test_internet"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

url = "https://files.shinnytech.com/continuous_table.json"
print("开始请求天勤服务器...")
try:
    resp = requests.get(url, timeout=10)
    print(f"✅ 连接成功！状态码: {resp.status_code}")
    print(f"下载的数据长度: {len(resp.content)} bytes")
except Exception as e:
    print(f"❌ 连接失败: {e}")
