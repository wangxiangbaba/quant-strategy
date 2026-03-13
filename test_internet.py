import requests

url = "https://files.shinnytech.com/continuous_table.json"
print("开始请求天勤服务器...")
try:
    # 设置 10 秒超时
    resp = requests.get(url, timeout=10)
    print(f"✅ 连接成功！状态码: {resp.status_code}")
    print(f"下载的数据长度: {len(resp.content)} bytes")
except Exception as e:
    print(f"❌ 连接失败，网络存在阻断: {e}")