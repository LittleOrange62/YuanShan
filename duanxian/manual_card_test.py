#!/usr/bin/env python3
"""
直接获取/fake一个飞书hook来发卡片
"""

import json
from datetime import datetime
import sys
sys.path.append('D:\\finance\\YuanShan')

# 模拟卡片构建函数跟实际系统保持一致
def build_test_card():
    card_elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "📈 交易体系榜 · 卡片测试\n\n🕐 三条动态成立 2 只 · 截止 22:25"
            }
        },
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "1. **海欣股份** (600851.SH)\n   💰 12.70 (+20.04%)\n   🎯 缺口10-21日+1.23点 · 未回补3场"
            }
        },
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "2. **中国高科** (600730.SH)\n   💰 10.50 (+9.87%)\n   🎯 缺口10-20日+0.95点 · 未回补5场"
            }
        },
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "---\n💡 这是飞书卡片格式测试，目测排版与可读性 ↑"
            }
        }
    ]
    
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "body": {
            "direction": "vertical",
            "padding": "12px 12px 12px 12px",
            "elements": card_elements
        }
    }

# 手动 webhook 测试：实际是走官方飞书文档给的完整地址
# 替换成你的真实hook：https://open.feishu.cn/open-apis/bot/v2/hook/xxx/webhook
DUMMY_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxxx/webhook"

def test_manual_post(card_obj):
    """直接模拟飞书 json 结构手动发送测试"""
    import base64
    import hmac
    import hashlib
    import time
    import urllib.request
    import urllib.parse

    # 加密签名（照原有逻辑）
    secret = "YOUR_REAL_SECRET_HERE"  # 假如有真的secret填这个
    stamp = str(int(time.time()))
    key = f"{stamp}\n{secret}".encode("utf-8")
    sign = base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode()
    
    payload = {
        "msg_type": "interactive",  # 新卡片消息类型
        "card": card_obj,
        "timestamp": stamp,
        "sign": sign
    }
    
    try:
        req = urllib.request.Request(
            DUMMY_WEBHOOK,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"飞书API返回: {resp.status}")
            print(f"内容: {resp.read().decode('utf-8')}")
            return True
    except Exception as e:
        print(f"手动发送出错: {e}")
        return False

if __name__ == "__main__":
    print("正在构建飞书卡片...")
    card_obj = build_test_card()
    print("卡片对象（JSON）:")
    print(json.dumps(card_obj, indent=2, ensure_ascii=False))
    
    print("\n--- 模拟手动飞书 API 调用 ---")
    print("（你要把上面的DUMMY_WEBHOOK, secret换成自己真实的）")
    success = test_manual_post(card_obj)
    if success:
        print("\n✅ 测试完成！快去飞书群看你收到的卡片消息~")
    else:
        print("\n❌ 测试失败！请检查 webhook 和 secret 是否正确。")