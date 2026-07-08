import os
import json
import pandas as pd
from tracker import check_market_drop, check_etf_signals, get_env_config

def run_tests():
    print("=== 開始測試台灣大盤下跌警報邏輯 ===")
    config = get_env_config()
    
    # 1. 測試大盤今日下跌
    yesterday_close = 23000.0
    
    # 初始狀態：預先填滿波段通知，以便「單純測試當日下跌」
    state = {
        "date": "2026-07-08",
        "yesterday_close": yesterday_close,
        "today_notified_drops": [],
        "wave_high": yesterday_close,
        "wave_notified_drops": [500, 600, 700, 800, 900, 1000],  # 屏蔽波段警報
        "signals_notified": {
            "0050.TW": "",
            "00646.TW": ""
        }
    }
    
    # 測試點 1：下跌 300 點 (未達 500)
    current_index = 22700.0
    res = check_market_drop(current_index, yesterday_close, state, config)
    print(f"價格: {current_index} (跌 {yesterday_close - current_index} 點)")
    print(f"-> 觸發通知數量: {len(res['messages'])}")
    assert len(res['messages']) == 0, "下跌未達 500 點不應發送通知"
    
    # 測試點 2：下跌 520 點 (突破 500)
    current_index = 22480.0
    res = check_market_drop(current_index, yesterday_close, state, config)
    print(f"價格: {current_index} (跌 {yesterday_close - current_index} 點)")
    print(f"-> 觸發通知數量: {len(res['messages'])}")
    for m in res['messages']:
        print(f"   [通知內容]:\n{m}")
    assert len(res['messages']) == 1, "跌破 500 點應發送 1 條通知"
    assert 500 in state["today_notified_drops"], "500 點關卡應記錄於今日已通知列表中"
    
    # 測試點 3：再次查詢，價格未變
    res = check_market_drop(current_index, yesterday_close, state, config)
    print(f"價格不變: {current_index}")
    print(f"-> 觸發通知數量: {len(res['messages'])}")
    assert len(res['messages']) == 0, "價格未變不應重複通知"
    
    # 測試點 4：繼續下跌至跌破 610 點 (突破 600 關卡)
    current_index = 22390.0
    res = check_market_drop(current_index, yesterday_close, state, config)
    print(f"價格: {current_index} (跌 {yesterday_close - current_index} 點)")
    print(f"-> 觸發通知數量: {len(res['messages'])}")
    for m in res['messages']:
        print(f"   [通知內容]:\n{m}")
    assert len(res['messages']) == 1, "再跌破 100 點 (600 關卡) 應再發送 1 條通知"
    assert 600 in state["today_notified_drops"], "600 點關卡應記錄於今日已通知列表中"
    
    # 測試點 5：暴跌至跌破 850 點 (一口氣跨過 700 與 800 關卡)
    current_index = 22120.0
    res = check_market_drop(current_index, yesterday_close, state, config)
    print(f"價格: {current_index} (跌 {yesterday_close - current_index} 點)")
    print(f"-> 觸發通知數量: {len(res['messages'])}")
    for m in res['messages']:
        print(f"   [通知內容]:\n{m}")
    assert len(res['messages']) == 1, "應發送 1 條整合通知"
    assert 700 in state["today_notified_drops"] and 800 in state["today_notified_drops"], "700 和 800 點關卡都應被標記為已通知"

    # 測試點 6：測試「波段累積下跌」（清空波段警報，屏蔽今日警報）
    print("-> 轉換為測試波段累積下跌")
    state["today_notified_drops"] = [500, 600, 700, 800, 900, 1000] # 屏蔽今日警報
    state["wave_notified_drops"] = []
    state["wave_high"] = 23000.0
    current_index = 22400.0 # 累積下跌 600 點
    res = check_market_drop(current_index, yesterday_close, state, config)
    print(f"累積價格: {current_index} (相較於波段最高 23000.0 跌 600 點)")
    print(f"-> 觸發通知數量: {len(res['messages'])}")
    for m in res['messages']:
        print(f"   [通知內容]:\n{m}")
    assert len(res['messages']) == 1, "波段下跌 600 點應發送 1 條通知"
    assert 500 in state["wave_notified_drops"] and 600 in state["wave_notified_drops"], "500 與 600 波段關卡應記錄"


    print("\n=== 開始測試 ETF 進場訊號邏輯 ===")
    # 模擬 0050 的技術指標數據
    # 情況 A：KD 低檔黃金交叉 (K 由 15 突破 D 18 到 K:21 D:19)
    etf_data = {
        "price": 150.0,
        "K": 21.0,
        "D": 19.0,
        "prev_K": 15.0,
        "prev_D": 18.0,
        "MA": 155.0,
        "Lower": 148.0 # 布林下軌
    }
    
    # K 升到 21 突破 D，但是 K=21 超過我們預設的 KD_LIMIT (20) 了嗎？
    # 根據我們的 tracker: K < LIMIT 且 D < LIMIT。這裡 K=21 超過了 20。
    # 讓我們將 K 設為 19.5, D 設為 19.0，前一天 K 設為 15.0, D 設為 18.0。這樣兩者都小於 20。
    etf_data["K"] = 19.5
    etf_data["D"] = 19.0
    
    signals = check_etf_signals("0050.TW", "元大台灣50", etf_data, state, config)
    print(f"測試 A (KD低檔交叉):")
    print(f"-> 觸發訊號數量: {len(signals)}")
    for s in signals:
        print(f"   [訊號內容]:\n{s}")
    assert len(signals) == 1, "應觸發 KD 黃金交叉進場訊號"
    
    # 情況 B：價格跌破布林下軌 (price 145.0 < Lower 148.0)
    # 重設訊號狀態
    state["signals_notified"]["0050.TW"] = ""
    etf_data_bb = {
        "price": 145.0,
        "K": 35.0, # 非低檔交叉
        "D": 40.0,
        "prev_K": 36.0,
        "prev_D": 39.0,
        "MA": 155.0,
        "Lower": 148.0
    }
    signals_bb = check_etf_signals("0050.TW", "元大台灣50", etf_data_bb, state, config)
    print(f"測試 B (跌破布林下軌):")
    print(f"-> 觸發訊號數量: {len(signals_bb)}")
    for s in signals_bb:
        print(f"   [訊號內容]:\n{s}")
    assert len(signals_bb) == 1, "應觸發跌破布林通道下軌進場訊號"

    print("\n所有核心邏輯單元測試通過！")

if __name__ == "__main__":
    run_tests()
