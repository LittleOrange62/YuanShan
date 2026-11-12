#!/usr/bin/env python3
"""
发送14:40「交易体系榜」飞书卡片测试
- 不带@所有人，直接飞群小测
- 1张卡比1条消息好看很多
"""

from datetime import datetime
import sys
import os
sys.path.append('D:\\finance\\YuanShan')

# 调用 notify 模块发送真实卡片的入口点
def build_card_message(rows, **kwargs):
    """在原始 notify 模块化用的卡片组装入口"""
    title = kwargs.get('title', '交易体系榜测试')
    at_all = kwargs.get('at_all', False)
    test = kwargs.get('test', False)
    
    card_elements = []
    
    # 主标题
    header_text = f"📈 {title}"
    if at_all:
        header_text += "\n\n<at id=all></at>"
    card_elements.append({
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": header_text
        }
    })
    
    # 副标题
    summary = f"三条动态成立 {len(rows.get('entry', []))} 只 · 截止 {datetime.now().strftime('%H:%M')}"
    card_elements.append({
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": f"🕐 {summary}"
        }
    })
    
    # 票行列表 —— 最多只渲染5行内部测试，防止飞书API报错
    entry_rows = rows.get('entry', [])[:3]
    for i, stock in enumerate(entry_rows, 1):
        stock_line = (
            f"{i}. **{stock.get('name', '')}** ({stock.get('security_code', '')})\n"
            f"   💰 {stock.get('close', 0)} ({stock.get('pct_change', 0):+.2f}%)\n"
            f"   🎯 缺口{stock.get('gap_date', datetime.now()).strftime('%m-%d')}日+{stock.get('gap_pct', 0):.2f}点 · 未回补{stock.get('gap_unfilled', 0)}场\n"
            f"   📊 回调最大量比{stock.get('pullback_max_vol_ratio', 0):.3f} · 阴线{stock.get('pullback_down_cnt', 0)}根\n"
            f"   🛡️ 止损参考{stock.get('stop_ref_hfq', 0)}（距{100.0 * (stock.get('stop_ref_hfq', 0)/stock.get('close', 1) - 1):+.1f}%）"
        )
        card_elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": stock_line
            }
        })
    
    # 结束说明
    card_elements.append({
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": "---\n💡 这是飞书卡片格式测试，目测排版与可读性 ↑"
        }
    })
    
    # 构造标准飞书卡对象
    card = {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "style": {
                "text_size": {
                    "normal_v2": {
                        "default": "normal",
                        "pc": "normal",
                        "mobile": "heading"
                    }
                }
            }
        },
        "body": {
            "direction": "vertical",
            "padding": "12px 12px 12px 12px",
            "elements": card_elements
        }
    }
    return card


if __name__ == "__main__":
    # 示例数据
    test_rows = {
        "entry": [
            {
                "name": "海欣股份",
                "security_code": "600851.SH",
                "close": 12.70,
                "pct_change": 20.04,
                "gap_pct": 1.23,
                "gap_date": datetime(2024, 10, 21),
                "gap_unfilled": 3,
                "pullback_max_vol_ratio": 2.345,
                "pullback_down_cnt": 2,
                "stop_ref_hfq": 13.35,
                "adjustment_factor": 1.0
            },
            {
                "name": "中国高科",
                "security_code": "600730.SH",
                "close": 10.50,
                "pct_change": 9.87,
                "gap_pct": 0.95,
                "gap_date": datetime(2024, 10, 20),
                "gap_unfilled": 5,
                "pullback_max_vol_ratio": 1.567,
                "pullback_down_cnt": 3,
                "stop_ref_hfq": 11.25,
                "adjustment_factor": 1.0
            }
        ]
    }
    
    # 生成卡片
    card_obj = build_card_message(test_rows, test=True)
    
    # 通过 notify 发送
    from duanxian.notify import send_to
    
    # 找一个真实飞书hook测试（这里假如有渠道配置就读，没有 print 一下结构）
    try:
        cfg = None
        keys = ["feishu_test_channel"]  # 实际改飞书webhook link进去
        result = send_to(
            keys=keys,
            title="📈 交易体系榜 · 卡片测试",
            body="**这是纯文本备用**",
            cfg=cfg,
            card=card_obj
        )
        print(f"卡片发送结果: {result}")
        if result.get('ok'):
            print("✅ 飞书卡片已发送（去群里查效果！）")
        else:
            print(f"❌ 发送失败: {result.get('detail')}")
    except Exception as e:
        print(f"发送异常: {e}")
        print(f"卡片内容（调试用）:\n{card_obj}")