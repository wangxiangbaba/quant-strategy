# Telegram 推送配置说明

## 一、创建机器人

1. 在 Telegram 搜索 **@BotFather**
2. 发送 `/newbot`，按提示设置机器人名称
3. 获得 **Bot Token**，形如 `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`

## 二、获取 chat_id

**私聊推送：**
1. 在 Telegram 搜索你的机器人，点击「开始」或发一条消息
2. 浏览器访问：`https://api.telegram.org/bot{你的token}/getUpdates`
3. 在返回的 JSON 中找到 `"chat":{"id":123456789}`，该数字即为 chat_id

**群组推送：**
1. 把机器人拉进目标群，设为管理员（可选，发消息不需要）
2. 在群里发一条消息（可 @ 机器人）
3. 再次访问 `getUpdates`，找到 `"chat":{"id":-1001234567890}`，负数即为群组 chat_id

## 三、配置

编辑 `telegram_notify.py`，填写：

```python
TELEGRAM_CONFIG = {
    "enabled": True,
    "bot_token": "123456789:ABCdefGHIjklMNOpqrsTUVwxyz",
    "chat_id": "你的chat_id",  # 数字，私聊为正数，群组为负数
}
```

## 四、推送内容

策略会向 Telegram 推送与飞书相同的内容：
- 启动、休市
- 开多/开空、止损、熔断
- 1 分钟账户状态
- 程序异常

## 五、网络说明

Telegram API 服务器在海外，若国内网络无法访问，可配置代理或使用可访问 Telegram 的环境运行策略。
