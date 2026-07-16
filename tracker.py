import os
import json
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, date, timezone, timedelta
from dotenv import load_dotenv

# 台北時區 (UTC+8)
TZ_TAIPEI = timezone(timedelta(hours=8))

def now_taipei():
    """取得台北時間的 datetime"""
    return datetime.now(TZ_TAIPEI)

def today_taipei():
    """取得台北時間的 date"""
    return datetime.now(TZ_TAIPEI).date()

# 載入環境變數
load_dotenv()

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")

def get_env_config():
    return {
        "LINE_CHANNEL_ACCESS_TOKEN": os.getenv("LINE_CHANNEL_ACCESS_TOKEN", ""),
        "LINE_USER_ID": os.getenv("LINE_USER_ID", ""),
        "DROP_THRESHOLD": float(os.getenv("DROP_THRESHOLD", 500)),
        "DROP_STEP": float(os.getenv("DROP_STEP", 100)),
        "USE_KD_STRATEGY": int(os.getenv("USE_KD_STRATEGY", 1)) == 1,
        "USE_BOLLINGER_STRATEGY": int(os.getenv("USE_BOLLINGER_STRATEGY", 1)) == 1,
        "KD_PERIOD": int(os.getenv("KD_PERIOD", 9)),
        "KD_LIMIT": float(os.getenv("KD_LIMIT", 20)),
        "BOLLINGER_PERIOD": int(os.getenv("BOLLINGER_PERIOD", 20)),
        "BOLLINGER_STD_DEV": float(os.getenv("BOLLINGER_STD_DEV", 2.0))
    }

def send_line_message(message: str) -> bool:
    """透過 LINE Messaging API 發送 Push Message"""
    config = get_env_config()
    token = config["LINE_CHANNEL_ACCESS_TOKEN"]
    user_id = config["LINE_USER_ID"]
    
    print(f"[LINE LOG] 發送訊息: {message}")
    
    if not token or not user_id:
        print("[LINE WARNING] 未設定 LINE_CHANNEL_ACCESS_TOKEN 或 LINE_USER_ID，僅在日誌中輸出。")
        return False
        
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }
    payload = {
        "to": user_id,
        "messages": [
            {
                "type": "text",
                "text": message
            }
        ]
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        if response.status_code == 200:
            print("[LINE SUCCESS] LINE 訊息發送成功。")
            return True
        else:
            print(f"[LINE ERROR] 發送失敗，HTTP 狀態碼: {response.status_code}, 回傳內容: {response.text}")
            return False
    except Exception as e:
        print(f"[LINE ERROR] 連線 LINE API 時發生異常: {e}")
        return False

def load_state() -> dict:
    """載入警報狀態"""
    default_signal_state = {
        "last_trigger_date": "",
        "cooldown_until": "",
        "trigger_count_this_year": 0
    }
    default_state = {
        "date": "",
        "yesterday_close": 0.0,
        "today_notified_drops": [],
        "wave_high": 0.0,
        "wave_notified_drops": [],
        "signals_notified": {
            code: dict(default_signal_state) for code in
            ["0050", "00646", "00692", "00850", "00662", "00830"]
        }
    }
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
                # 補齊可能缺失的 key
                for k, v in default_state.items():
                    if k not in state:
                        state[k] = v
                return state
        except Exception as e:
            print(f"[STATE ERROR] 讀取狀態檔失敗，使用預設值: {e}")
    return default_state

def save_state(state: dict):
    """儲存警報狀態"""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[STATE ERROR] 寫入狀態檔失敗: {e}")

# 分組標籤
GROUP_LABELS = {
    "low_vol": "低波動組",
    "mid_vol": "中波動組",
    "high_vol": "高波動組",
}

# 追蹤的 ETF 清單定義（含分組參數）
ETF_LIST = {
    "00646": {"name": "元大S&P500",     "yf": "00646.TW", "exchange": "tse",
              "group": "low_vol",  "kd_limit": 25, "bb_std": 1.8},
    "0050":  {"name": "元大台灣50",     "yf": "0050.TW",  "exchange": "tse",
              "group": "mid_vol",  "kd_limit": 20, "bb_std": 2.0},
    "00692": {"name": "富邦公司治理",   "yf": "00692.TW", "exchange": "tse",
              "group": "mid_vol",  "kd_limit": 20, "bb_std": 2.0},
    "00850": {"name": "元大臺灣ESG永續", "yf": "00850.TW", "exchange": "tse",
              "group": "mid_vol",  "kd_limit": 20, "bb_std": 2.0},
    "00662": {"name": "富邦NASDAQ",     "yf": "00662.TW", "exchange": "tse",
              "group": "high_vol", "kd_limit": 15, "bb_std": 2.3},
    "00830": {"name": "國泰費城半導體", "yf": "00830.TW", "exchange": "tse",
              "group": "high_vol", "kd_limit": 15, "bb_std": 2.3},
}

# 冷卻期天數（交易日）
COOLDOWN_TRADING_DAYS = 20

def get_twse_realtime() -> dict:
    """從證交所 API 獲取即時大盤與所有追蹤 ETF 的盤中數據"""
    # 動態建立查詢字串
    etf_query = "|".join([f"{info['exchange']}_{code}.tw" for code, info in ETF_LIST.items()])
    url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_t00.tw|{etf_query}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if "msgArray" in data and len(data["msgArray"]) > 0:
                result = {}
                for item in data["msgArray"]:
                    code = item.get("c")
                    # z: 當前成交價 (大盤可能需要用特別處理，有些時段 z 是空字串，可以用 tv 或是昨日收盤 y + 漲跌計算)
                    # y: 昨收價
                    price_str = item.get("z", "")
                    price = 0.0
                    
                    if price_str and price_str != "-":
                        try:
                            price = float(price_str.replace(",", ""))
                        except ValueError:
                            pass
                            
                    # 若 z 為空或 "-" (盤中成交揭示空隙)，改用最佳五檔買一賣一平均值
                    if price <= 0:
                        a_str = item.get("a", "").split("_")[0]
                        b_str = item.get("b", "").split("_")[0]
                        
                        a_val = 0.0
                        b_val = 0.0
                        try:
                            a_val = float(a_str.replace(",", "")) if a_str and a_str != "-" else 0.0
                        except ValueError:
                            pass
                        try:
                            b_val = float(b_str.replace(",", "")) if b_str and b_str != "-" else 0.0
                        except ValueError:
                            pass
                            
                        if a_val > 0 and b_val > 0:
                            price = (a_val + b_val) / 2.0
                        elif a_val > 0:
                            price = a_val
                        elif b_val > 0:
                            price = b_val
                            
                    # 若都拿不到，最後使用昨日收盤價
                    if price <= 0:
                        try:
                            price = float(item.get("y", "0").replace(",", ""))
                        except ValueError:
                            price = 0.0
                            
                    try:
                        yesterday_close = float(item.get("y", "0").replace(",", ""))
                    except ValueError:
                        yesterday_close = 0.0
                        
                    name = item.get("n", "")
                    result[code] = {
                        "price": price,
                        "yesterday_close": yesterday_close,
                        "name": name,
                        "time": item.get("tlong", "")
                    }
                return result
    except Exception as e:
        print(f"[TWSE API ERROR] 讀取證交所即時 API 異常: {e}")
    return {}

def calculate_kd(df: pd.DataFrame, period: int = 9) -> pd.DataFrame:
    """計算 KD (9, 3, 3) 指標"""
    df = df.copy()
    if len(df) < period:
        df['K'] = 50.0
        df['D'] = 50.0
        return df
        
    # 9天內最高與最低價
    df['Low_Min'] = df['Low'].rolling(window=period).min()
    df['High_Max'] = df['High'].rolling(window=period).max()
    
    # RSV = (今日收盤 - 9天最低) / (9天最高 - 9天最低) * 100
    df['RSV'] = (df['Close'] - df['Low_Min']) / (df['High_Max'] - df['Low_Min']) * 100
    df['RSV'] = df['RSV'].fillna(50.0) # 處理最高等於最低的情況
    
    k_vals = []
    d_vals = []
    current_k = 50.0
    current_d = 50.0
    
    for rsv in df['RSV']:
        current_k = (2/3) * current_k + (1/3) * rsv
        current_d = (2/3) * current_d + (1/3) * current_k
        k_vals.append(current_k)
        d_vals.append(current_d)
        
    df['K'] = k_vals
    df['D'] = d_vals
    return df

def calculate_bollinger_bands(df: pd.DataFrame, period: int = 20, std_dev: float = 2.0) -> pd.DataFrame:
    """計算布林通道"""
    df = df.copy()
    if len(df) < period:
        df['MA'] = df['Close']
        df['Upper'] = df['Close']
        df['Lower'] = df['Close']
        return df
        
    df['MA'] = df['Close'].rolling(window=period).mean()
    df['Std'] = df['Close'].rolling(window=period).std()
    df['Upper'] = df['MA'] + std_dev * df['Std']
    df['Lower'] = df['MA'] - std_dev * df['Std']
    return df

def get_historical_and_indicators(symbol: str, config: dict) -> dict:
    """獲取歷史日 K 線並計算最新的技術指標（供圖表用）"""
    ticker = yf.Ticker(symbol)
    df = ticker.history(period="3mo")
    
    if df.empty:
        print(f"[YFINANCE ERROR] 無法取得 {symbol} 的歷史數據")
        return {}
        
    # 計算日線 KD 與布林（供前端圖表顯示）
    df = calculate_kd(df, period=config["KD_PERIOD"])
    df = calculate_bollinger_bands(df, period=config["BOLLINGER_PERIOD"], std_dev=config["BOLLINGER_STD_DEV"])
    
    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else latest
    
    return {
        "df": df,
        "price": float(latest["Close"]),
        "prev_price": float(prev["Close"]),
        "K": float(latest["K"]),
        "D": float(latest["D"]),
        "prev_K": float(prev["K"]),
        "prev_D": float(prev["D"]),
        "MA": float(latest["MA"]),
        "Upper": float(latest["Upper"]),
        "Lower": float(latest["Lower"])
    }

def get_weekly_indicators(symbol: str, etf_meta: dict) -> dict:
    """獲取週線指標（KD、布林、52 週高點）用於進場訊號判斷"""
    ticker = yf.Ticker(symbol)
    # 拉取 18 個月日線，確保有足夠資料算 52 週高點與 20 週布林
    df_daily = ticker.history(period="18mo")
    
    if df_daily.empty or len(df_daily) < 20:
        print(f"[WEEKLY ERROR] 無法取得 {symbol} 足夠的歷史數據")
        return {}
    
    # 計算 52 週（約 252 個交易日）最高價
    high_52w = float(df_daily['High'].tail(252).max()) if len(df_daily) >= 252 else float(df_daily['High'].max())
    current_price = float(df_daily.iloc[-1]['Close'])
    drop_from_high = ((high_52w - current_price) / high_52w) * 100  # 正值=跌幅百分比
    
    # 日線轉週線
    df_weekly = df_daily.resample('W').agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
        'Volume': 'sum'
    }).dropna()
    
    if len(df_weekly) < 10:
        print(f"[WEEKLY ERROR] {symbol} 週線資料不足")
        return {}
    
    # 用分組參數計算週線 KD 和布林
    kd_limit = etf_meta.get("kd_limit", 20)
    bb_std = etf_meta.get("bb_std", 2.0)
    
    df_weekly = calculate_kd(df_weekly, period=9)  # 9 週 KD
    df_weekly = calculate_bollinger_bands(df_weekly, period=20, std_dev=bb_std)  # 20 週布林
    
    latest = df_weekly.iloc[-1]
    prev = df_weekly.iloc[-2] if len(df_weekly) > 1 else latest
    
    return {
        "df_weekly": df_weekly,
        "price": current_price,
        "weekly_K": float(latest["K"]),
        "weekly_D": float(latest["D"]),
        "prev_weekly_K": float(prev["K"]),
        "prev_weekly_D": float(prev["D"]),
        "weekly_MA": float(latest["MA"]),
        "weekly_Upper": float(latest["Upper"]),
        "weekly_Lower": float(latest["Lower"]),
        "high_52w": high_52w,
        "drop_from_high_pct": drop_from_high,
        "kd_limit": kd_limit,
        "bb_std": bb_std,
    }

def check_market_drop(current_index: float, yesterday_close: float, state: dict, config: dict) -> dict:
    """檢查大盤下跌警報邏輯"""
    today_str = today_taipei().isoformat()
    messages = []
    
    # 1. 跨日檢查與狀態重設
    if state["date"] != today_str:
        state["date"] = today_str
        state["yesterday_close"] = yesterday_close
        state["today_notified_drops"] = []
        # wave_high 如果是 0，則以昨日收盤價初始化
        if state["wave_high"] <= 0:
            state["wave_high"] = yesterday_close
        # 重設 ETF 進場訊號通知狀態
        for key in state.get("signals_notified", {}):
            state["signals_notified"][key] = ""
        print(f"[STATE RESET] 偵測到新的一天 {today_str}。昨收價設為 {yesterday_close}，波段最高點為 {state['wave_high']}")
    
    # 2. 每次執行時，都用 TWSE API 回傳的 yesterday_close 校正 state
    #    防止因排程時區錯誤或服務重啟，導致 state 中的 yesterday_close 為過期值
    if yesterday_close > 0 and abs(state["yesterday_close"] - yesterday_close) > 1.0:
        print(f"[STATE FIX] 校正 yesterday_close: {state['yesterday_close']:.2f} -> {yesterday_close:.2f} (來自 TWSE API)")
        state["yesterday_close"] = yesterday_close
    
    y_close = state["yesterday_close"]
    if y_close <= 0:
        y_close = yesterday_close
        state["yesterday_close"] = y_close
        
    # 2. 當日下跌計算
    today_drop = y_close - current_index
    drop_threshold = config["DROP_THRESHOLD"]
    drop_step = config["DROP_STEP"]
    
    # 檢查是否達到當日下跌 500 點門檻
    if today_drop >= drop_threshold:
        # 計算目前應該觸發的階層 (例如：500, 600, 700...)
        current_level = int(drop_threshold + ((today_drop - drop_threshold) // drop_step) * drop_step)
        
        # 檢查該階層是否已經通知過
        if current_level not in state["today_notified_drops"]:
            # 將比 current_level 小但沒發過的一併補上，避免跳空大跌漏發
            levels_to_notify = []
            for lv in range(int(drop_threshold), current_level + 1, int(drop_step)):
                if lv not in state["today_notified_drops"]:
                    levels_to_notify.append(lv)
                    state["today_notified_drops"].append(lv)
            
            if levels_to_notify:
                max_level = max(levels_to_notify)
                msg = (
                    f"⚠️【大盤今日暴跌警報】\n"
                    f"台股大盤今日跌幅已擴大！\n"
                    f"📉 今日下跌點數：{today_drop:.2f} 點 (已突破 {max_level} 點關卡)\n"
                    f"📊 當前大盤指數：{current_index:.2f}\n"
                    f"↩️ 昨日收盤指數：{y_close:.2f}"
                )
                messages.append(msg)
                
    # 3. 波段累積下跌計算 (波段最高點 - 目前價)
    # 如果目前指數高於波段最高點，則更新波段最高點，並重設波段通知狀態
    if current_index > state["wave_high"]:
        state["wave_high"] = current_index
        state["wave_notified_drops"] = []
        print(f"[WAVE UPDATE] 大盤創波段新高：{current_index:.2f}，重設波段最高點與通知。")
        
    wave_high = state["wave_high"]
    cumulative_drop = wave_high - current_index
    
    if cumulative_drop >= drop_threshold:
        current_wave_level = int(drop_threshold + ((cumulative_drop - drop_threshold) // drop_step) * drop_step)
        if current_wave_level not in state["wave_notified_drops"]:
            levels_to_notify = []
            for lv in range(int(drop_threshold), current_wave_level + 1, int(drop_step)):
                if lv not in state["wave_notified_drops"]:
                    levels_to_notify.append(lv)
                    state["wave_notified_drops"].append(lv)
            
            if levels_to_notify:
                max_level = max(levels_to_notify)
                msg = (
                    f"🚨【大盤波段累積下跌警報】\n"
                    f"台股大盤波段累積跌幅已達警戒！\n"
                    f"📉 累積下跌點數：{cumulative_drop:.2f} 點 (已突破 {max_level} 點關卡)\n"
                    f"📊 當前大盤指數：{current_index:.2f}\n"
                    f"🏔️ 波段最高指數：{wave_high:.2f}"
                )
                messages.append(msg)
                
    return {
        "messages": messages,
        "today_drop": today_drop,
        "cumulative_drop": cumulative_drop
    }

def check_etf_signals_weekly(code: str, etf_meta: dict, weekly_data: dict, state: dict, config: dict) -> list:
    """用週線指標檢查 ETF 進場訊號（含冷卻期 + 跌幅分層 + 決策型通知）"""
    name = etf_meta["name"]
    group = etf_meta["group"]
    group_label = GROUP_LABELS.get(group, group)
    today_str = today_taipei().isoformat()
    messages = []
    
    # 確保 signals_notified 中有該 code 的 dict 結構
    if code not in state.get("signals_notified", {}) or isinstance(state["signals_notified"].get(code), str):
        state.setdefault("signals_notified", {})[code] = {
            "last_trigger_date": "",
            "cooldown_until": "",
            "trigger_count_this_year": 0
        }
    
    sig_state = state["signals_notified"][code]
    
    # 年度重設（每年 1 月 1 日）
    current_year = str(now_taipei().year)
    last_trigger = sig_state.get("last_trigger_date", "")
    if last_trigger and not last_trigger.startswith(current_year):
        sig_state["trigger_count_this_year"] = 0
    
    # 取得週線資料
    price = weekly_data["price"]
    kd_limit = weekly_data["kd_limit"]
    drop_pct = weekly_data["drop_from_high_pct"]
    high_52w = weekly_data["high_52w"]
    
    triggered_strategies = []
    
    # 策略 A：週線 KD 低檔黃金交叉
    if config.get("USE_KD_STRATEGY", True):
        k = weekly_data["weekly_K"]
        d = weekly_data["weekly_D"]
        prev_k = weekly_data["prev_weekly_K"]
        prev_d = weekly_data["prev_weekly_D"]
        
        if prev_k <= prev_d and k > d and k < kd_limit and d < kd_limit:
            triggered_strategies.append(f"週 KD 低檔黃金交叉 (K:{k:.1f} 突破 D:{d:.1f}，門檻 {kd_limit})")
    
    # 策略 B：週線跌破布林下軌
    if config.get("USE_BOLLINGER_STRATEGY", True):
        lower = weekly_data["weekly_Lower"]
        if price < lower:
            triggered_strategies.append(f"週線跌破布林下軌 (價格:{price:.2f} < 下軌:{lower:.2f}，σ={weekly_data['bb_std']})")
    
    if not triggered_strategies:
        return []
    
    # 檢查冷卻期
    in_cooldown = False
    cooldown_until = sig_state.get("cooldown_until", "")
    if cooldown_until and today_str <= cooldown_until:
        in_cooldown = True
    
    # 跌幅分層判斷
    if drop_pct < 10:
        action = "📋 僅列入觀察，不執行額外加碼（跌幅 < 10%）"
        tier = 0
    elif drop_pct < 15:
        action = "💰 建議執行第 1 層加碼（當月基本扣款 0.5 倍）"
        tier = 1
    elif drop_pct < 20:
        action = "💰💰 建議執行第 2 層加碼（當月基本扣款 1.0 倍）"
        tier = 2
    else:
        action = "💰💰💰 建議執行第 3 層加碼（當月基本扣款 1.5 倍）"
        tier = 3
    
    # 高波動組限制
    high_vol_note = ""
    if group == "high_vol" and tier >= 2:
        high_vol_note = "\n⚠️ 高波動組限制：單次加碼上限為基本扣款 1 倍"
    
    # 冷卻期標註
    if in_cooldown:
        cooldown_note = f"⏸️ 冷卻期內（至 {cooldown_until}），不建議重複加碼"
    else:
        cooldown_note = "✅ 不在冷卻期"
    
    strategies_str = " / ".join(triggered_strategies)
    trigger_count = sig_state.get("trigger_count_this_year", 0) + 1
    
    msg = (
        f"📊【{name} ({code}) — 進場訊號觸發】\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🏷️ 分組：{group_label}\n"
        f"🔔 觸發策略：{strategies_str}\n"
        f"📉 距 52 週高點：-{drop_pct:.1f}%（高點 {high_52w:.2f}）\n"
        f"⏱️ 冷卻期：{cooldown_note}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"💡 {action}{high_vol_note}\n"
        f"💲 目前價格：{price:.2f}\n"
        f"📅 本年度第 {trigger_count} 次觸發\n"
        f"⏰ {now_taipei().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    messages.append(msg)
    
    # 更新狀態（不在冷卻期才更新觸發記錄）
    if not in_cooldown:
        sig_state["last_trigger_date"] = today_str
        sig_state["trigger_count_this_year"] = trigger_count
        # 設定冷卻期：20 個交易日 ≈ 28 天
        cooldown_end = (now_taipei() + timedelta(days=28)).date().isoformat()
        sig_state["cooldown_until"] = cooldown_end
    
    return messages

def run_tracking_cycle() -> dict:
    """執行一次完整的監控與分析循環"""
    print(f"\n[TRACKER] 開始執行監控循環：{now_taipei().isoformat()}")
    config = get_env_config()
    state = load_state()
    
    # 1. 取得即時數據與歷史數據
    # 台股即時
    realtime = get_twse_realtime()
    
    # 大盤指數 (t00)
    t00_info = realtime.get("t00")
    if t00_info:
        current_index = t00_info["price"]
        yesterday_close = t00_info["yesterday_close"]
    else:
        # 備份：使用 yfinance 抓取
        print("[TRACKER] 無法取得證交所即時大盤資料，改用 yfinance 備份數據")
        try:
            twii = yf.Ticker("^TWII")
            hist = twii.history(period="2d")
            if not hist.empty:
                current_index = float(hist.iloc[-1]["Close"])
                yesterday_close = float(hist.iloc[-2]["Close"]) if len(hist) > 1 else current_index
            else:
                current_index, yesterday_close = 0.0, 0.0
        except Exception as e:
            print(f"[YFINANCE ERROR] 獲取 ^TWII 異常: {e}")
            current_index, yesterday_close = 0.0, 0.0
            
    # 2. 獲取所有追蹤 ETF 的日線指標（圖表用）和週線指標（訊號用）
    etf_daily = {}
    etf_weekly = {}
    for code, meta in ETF_LIST.items():
        try:
            # 日線（前端圖表）
            info = get_historical_and_indicators(meta["yf"], config)
            if info:
                if code in realtime:
                    info["price"] = realtime[code]["price"]
                etf_daily[code] = info
        except Exception as e:
            print(f"[TRACKER ERROR] 獲取 {code} 日線指標失敗: {e}")
        try:
            # 週線（進場訊號）
            weekly = get_weekly_indicators(meta["yf"], meta)
            if weekly:
                if code in realtime:
                    weekly["price"] = realtime[code]["price"]
                etf_weekly[code] = weekly
        except Exception as e:
            print(f"[TRACKER ERROR] 獲取 {code} 週線指標失敗: {e}")
        
    all_notifications = []
    
    # 3. 檢查大盤下跌
    drop_info = {}
    if current_index > 0 and yesterday_close > 0:
        drop_result = check_market_drop(current_index, yesterday_close, state, config)
        all_notifications.extend(drop_result["messages"])
        drop_info = {
            "current_index": current_index,
            "yesterday_close": yesterday_close,
            "today_drop": drop_result["today_drop"],
            "cumulative_drop": drop_result["cumulative_drop"]
        }
    else:
        print("[TRACKER WARNING] 大盤價格異常，跳過大盤跌幅檢查。")
        drop_info = {
            "current_index": current_index,
            "yesterday_close": yesterday_close,
            "today_drop": 0.0,
            "cumulative_drop": 0.0
        }
        
    # 4. 檢查所有 ETF 進場訊號（用週線指標）
    etf_info = {}
    for code, meta in ETF_LIST.items():
        daily = etf_daily.get(code)
        weekly = etf_weekly.get(code)
        
        # 基本卡片資料來自日線
        if daily:
            card_data = {
                "price": daily["price"],
                "K": daily["K"],
                "D": daily["D"],
                "MA": daily["MA"],
                "Lower": daily["Lower"],
                "signal": False,
                "group": meta["group"],
                "group_label": GROUP_LABELS.get(meta["group"], ""),
            }
            
            # 週線資料（補充到卡片）
            if weekly:
                card_data.update({
                    "weekly_K": weekly["weekly_K"],
                    "weekly_D": weekly["weekly_D"],
                    "weekly_MA": weekly["weekly_MA"],
                    "weekly_Lower": weekly["weekly_Lower"],
                    "high_52w": weekly["high_52w"],
                    "drop_from_high_pct": weekly["drop_from_high_pct"],
                })
                
                # 檢查冷卻期狀態
                sig_state = state.get("signals_notified", {}).get(code, {})
                cooldown_until = sig_state.get("cooldown_until", "") if isinstance(sig_state, dict) else ""
                card_data["in_cooldown"] = bool(cooldown_until and today_taipei().isoformat() <= cooldown_until)
                card_data["cooldown_until"] = cooldown_until
                card_data["trigger_count"] = sig_state.get("trigger_count_this_year", 0) if isinstance(sig_state, dict) else 0
                
                # 用週線訊號判斷進場
                sig = check_etf_signals_weekly(code, meta, weekly, state, config)
                all_notifications.extend(sig)
                card_data["signal"] = len(sig) > 0
            
            etf_info[code] = card_data
        
    # 5. 發送 LINE 通知
    for msg in all_notifications:
        send_line_message(msg)
        
    # 儲存狀態
    save_state(state)
    
    return {
        "timestamp": now_taipei().isoformat(),
        "drop_info": drop_info,
        "etf_info": etf_info,
        "notified_messages": all_notifications,
        "state": state
    }
