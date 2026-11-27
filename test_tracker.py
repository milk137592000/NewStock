from datetime import timedelta
import pandas as pd
from tracker import (
    check_market_drop,
    check_etf_signals_weekly,
    evaluate_etf_entry,
    calculate_rsi,
    calculate_kd,
    _adjust_unadjusted_splits,
    get_env_config,
    ETF_LIST,
    today_taipei,
)


def _make_weekly_sample(**overrides):
    data = {
        "price": 100.0,
        "high_52w": 115.0,
        "drop_from_high_pct": 13.04,
        "weekly_K": 30.0,
        "weekly_D": 35.0,
        "prev_weekly_K": 32.0,
        "prev_weekly_D": 34.0,
        "weekly_RSI": 42.0,
        "weekly_MA": 106.0,
        "weekly_Upper": 114.0,
        "weekly_Lower": 98.0,
        "bias_20w": -5.66,
        "daily_K": 35.0,
        "daily_D": 38.0,
        "prev_daily_K": 36.0,
        "prev_daily_D": 37.0,
        "daily_RSI": 42.0,
        "MA60": 105.0,
        "MA200": 104.0,
        "kd_limit": 20.0,
        "bb_std": 2.0,
    }
    data.update(overrides)
    return data


def _make_clean_state():
    return {
        "date": today_taipei().isoformat(),
        "yesterday_close": 23000.0,
        "today_notified_drops": [],
        "wave_high": 23000.0,
        "wave_notified_drops": [500, 600, 700, 800, 900, 1000],
        "signals_notified": {},
        "last_biweekly_date": "",
    }


def run_tests():
    print("=== 1. 測試技術指標計算 (RSI / KD / 股票分割還原) ===")
    closes = [100 + i for i in range(20)]
    df_up = pd.DataFrame({
        "Open": closes,
        "High": [c + 1 for c in closes],
        "Low": [c - 1 for c in closes],
        "Close": closes,
    })
    df_rsi = calculate_rsi(df_up, period=14)
    assert df_rsi["RSI"].iloc[-1] == 100.0, f"全漲波段 RSI 應為 100，實際為 {df_rsi['RSI'].iloc[-1]}"

    df_kd = calculate_kd(df_up, period=9)
    assert df_kd["K"].iloc[-1] > 80, "連續上漲波段 K 值應大於 80"

    # 測試 1拆5 未還原股價自動校正
    split_closes = [110.0, 112.0, 113.5, 22.8, 22.6, 22.9]
    df_split = pd.DataFrame({
        "Open": split_closes,
        "High": [c + 0.5 for c in split_closes],
        "Low": [c - 0.5 for c in split_closes],
        "Close": split_closes,
    })
    df_adj = _adjust_unadjusted_splits(df_split, symbol="00662.TW")
    assert 21.0 <= df_adj["Close"].iloc[0] <= 23.0, f"1拆5歷史價應縮放約5倍，實際為 {df_adj['Close'].iloc[0]}"

    print("=== 2. 測試新版 ETF 多週期入場規則 ===")
    config = get_env_config()
    config["USE_KD_STRATEGY"] = True
    config["USE_BOLLINGER_STRATEGY"] = True
    config["KD_LIMIT"] = 20.0

    # 案例 A：低波動組 00646 僅跌幅 4.8% (<7%)，即使跌破布林下軌也只觀察，不可發訊號或鎖冷卻期
    state = _make_clean_state()
    w_tier0 = _make_weekly_sample(
        price=75.9,
        high_52w=79.7,
        drop_from_high_pct=4.77,
        weekly_Lower=76.5,
        weekly_MA=77.1,
    )
    eval0 = evaluate_etf_entry("00646", ETF_LIST["00646"], w_tier0, config)
    assert eval0["tier"] == 0 and eval0["observation_only"] and not eval0["actionable"]
    msgs0 = check_etf_signals_weekly("00646", ETF_LIST["00646"], w_tier0, state, config)
    assert len(msgs0) == 0, "Tier 0 僅觀察，不應發送買進通知"
    assert state["signals_notified"]["00646"]["cooldown_until"] == "", "Tier 0 不得鎖定冷卻期"

    # 案例 B：中波動組 0050 回檔 12.5% (第1層) + 週 KD 低檔金叉 -> 觸發並鎖定第 1 層冷卻
    w_0050_t1 = _make_weekly_sample(
        price=102.0,
        high_52w=116.6,
        drop_from_high_pct=12.5,
        weekly_K=22.0,
        weekly_D=20.5,
        prev_weekly_K=18.0,
        prev_weekly_D=21.0,
        weekly_RSI=34.0,
        MA200=105.0,
    )
    msgs_t1 = check_etf_signals_weekly("0050", ETF_LIST["0050"], w_0050_t1, state, config)
    assert len(msgs_t1) == 1, "第 1 層應觸發進場通知"
    assert state["signals_notified"]["0050"]["last_trigger_tier"] == 1
    assert "0.5 倍" in msgs_t1[0]

    # 同天再次呼叫應去重
    assert len(check_etf_signals_weekly("0050", ETF_LIST["0050"], w_0050_t1, state, config)) == 0

    # 案例 C：冷卻期內若隔天續跌擴大到第 2 層 (-18.0%) -> 階梯升級解鎖
    yesterday_str = (today_taipei() - timedelta(days=2)).isoformat()
    state["signals_notified"]["0050"]["last_notified_date"] = yesterday_str
    state["signals_notified"]["0050"]["last_trigger_date"] = yesterday_str
    w_0050_t2 = _make_weekly_sample(
        price=95.6,
        high_52w=116.6,
        drop_from_high_pct=18.0,
        weekly_K=20.0,
        weekly_D=25.0,
        weekly_RSI=31.0,
        weekly_MA=104.0,
        MA200=105.0,
    )
    msgs_t2 = check_etf_signals_weekly("0050", ETF_LIST["0050"], w_0050_t2, state, config)
    assert len(msgs_t2) == 1 and "階梯升級" in msgs_t2[0], "跌入更高加碼層級應解鎖階梯補買通知"
    assert state["signals_notified"]["0050"]["last_trigger_tier"] == 2

    # 案例 D：高波動組 00830 在 -12% 時屬正常波動 (Tier 0)，必須 >= 15% 才入第 1 層；>= 30% 第 3 層顯示無矛盾 1.0 倍上限
    w_00830_12 = _make_weekly_sample(drop_from_high_pct=12.0, weekly_RSI=31.0)
    assert evaluate_etf_entry("00830", ETF_LIST["00830"], w_00830_12, config)["tier"] == 0
    w_00830_34 = _make_weekly_sample(
        price=67.45,
        high_52w=102.2,
        drop_from_high_pct=34.0,
        weekly_K=17.7,
        weekly_D=26.3,
        weekly_RSI=34.0,
        weekly_MA=81.7,
        weekly_Lower=65.1,
        MA200=85.0,
    )
    eval_830 = evaluate_etf_entry("00830", ETF_LIST["00830"], w_00830_34, config)
    assert eval_830["tier"] == 3 and eval_830["actionable"], "緩跌 -34% 且處於週線下與低 RSI 應被辨識為第 3 層買點"
    assert "1.5 倍" not in eval_830["tier_action"], "高波動組不應出現矛盾的 1.5 倍建議"

    print("=== 3. 測試大盤下跌警報回歸檢查 ===")
    res = check_market_drop(22700.0, 23000.0, state, config)
    assert len(res["messages"]) == 0

    print("\n所有測試皆通過！")


if __name__ == "__main__":
    run_tests()
