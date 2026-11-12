"""交易体系榜飞书卡片测试样例。"""

from datetime import datetime

import sys
sys.path.append('D:\\finance\\YuanShan')

# 模拟 t07 传来的结果
test_rows = {
    "entry": [
        {
            "name": "海欣股份",
            "security_code": "600851.SH",
            "security_name": "海欣股份",
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
            "security_name": "中国高科",
            "close": 10.50,
            "pct_change": 9.87,
            "gap_pct": 0.95,
            "gap_date": datetime(2024, 10, 20),
            "gap_unfilled": 5,
            "pullback_max_vol_ratio": 1.567,
            "pullback_down_cnt": 3,
            "stop_ref_hfq": 11.25,
            "adjustment_factor": 1.0
        },
        {
            "name": "中视传媒",
            "security_code": "600088.SH",
            "security_name": "中视传媒",
            "close": 15.80,
            "pct_change": 15.32,
            "gap_pct": 2.45,
            "gap_date": datetime(2024, 10, 19),
            "gap_unfilled": 2,
            "pullback_max_vol_ratio": 3.123,
            "pullback_down_cnt": 4,
            "stop_ref_hfq": 17.25,
            "adjustment_factor": 1.0
        }
    ]
}

def build_card_message(rows, *, title="交易体系榜测试", at_all=False, test=True):
    """组装飞书卡片的 msg_type:interactive 格式"""
    header_text = f"📈 {title}"
    if at_all:
        header_text += "\n\n<at id=all></at>"
    
    card_elements = []
    
    # 主标题
    card_elements.append({
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": header_text
        }
    })
    
    # 副标题
    summary = f"三条动态成立 {len(rows['entry'])} 只 · 截止 {datetime.now().strftime('%H:%M')}"
    card_elements.append({
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": f"🕐 {summary}"
        }
    })
    
    # 票行列表
    for i, stock in enumerate(rows['entry'][:5], 1):
        stock_line = (
            f"{i}. **{stock['name']}** ({stock['security_code']})\n"
            f"   💰 {stock['close']} ({stock['pct_change']:+.2f}%)\n"
            f"   🎯 缺口{stock['gap_date'].strftime('%m-%d')}日+{stock['gap_pct']:.2f}点 · 未回补{stock['gap_unfilled']}场\n"
            f"   📊 回调最大量比{stock['pullback_max_vol_ratio']:.3f} · 阴线{stock['pullback_down_cnt']}根\n"
            f"   🛡️ 止损参考{stock['stop_ref_hfq']}（距{100.0 * (stock['stop_ref_hfq']/stock['close'] - 1):+.1f}%）"
        )
        card_elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": stock_line
            }
        })
    
    # 测试用额外说明
    if test:
        card_elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "---\n💡 这是测试用飞书卡片。表格、链接、颜色等高级功能待验证。"
            }
        })
    
    # 尾注
    footer = (
        "\n---\n"
        "🔗 口径：sysentry\n"
        "📊 来源：TQ快照测试\n"
        "⚠️ 数据仅供测试，行情以真实盘面为准"
    )
    card_elements.append({
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": footer
        }
    })
    
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
    print("飞书卡片调试:")
    card_obj = build_card_message(test_rows, test=True)
    print("#### 卡片结构 ####")
    import json
    print(json.dumps(card_obj, indent=2, ensure_ascii=False))
    print("\n#### 卡片里的 Markdown ####")
    for i, elem in enumerate(card_obj["body"]["elements"], 1):
        if "content" in elem.get("text", {}):
            print(f"元素{i}:\n{elem['text']['content']}\n---")