import os
import json
import logging
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime, timedelta

from tracker import run_tracking_cycle, get_env_config, send_line_message, get_historical_and_indicators, now_taipei, ETF_LIST, GROUP_LABELS
from scheduler import start_scheduler, shutdown_scheduler

# 設定日誌
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("main")

app = FastAPI(title="台灣大盤與 ETF 追蹤通知 App")

# 解決跨域問題
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 快取機制，避免前端輪詢時過於頻繁請求 API (設為 20 秒，以適應每分鐘自動更新)
CACHE_DURATION = timedelta(seconds=20)
last_fetch_time = None
cached_data = None

# 目錄結構定義
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

# 確保目錄存在
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(os.path.join(STATIC_DIR, "css"), exist_ok=True)
os.makedirs(os.path.join(STATIC_DIR, "js"), exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)

# 掛載靜態檔案 (如果 static 資料夾有檔案的話)
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

class ConfigUpdate(BaseModel):
    LINE_CHANNEL_ACCESS_TOKEN: str
    LINE_USER_ID: str
    DROP_THRESHOLD: float
    DROP_STEP: float
    USE_KD_STRATEGY: bool
    USE_BOLLINGER_STRATEGY: bool
    KD_LIMIT: float

@app.on_event("startup")
async def startup_event():
    # 啟動排程器
    start_scheduler()
    # 啟動時先跑一次以更新快取
    global last_fetch_time, cached_data
    try:
        cached_data = run_tracking_cycle()
        last_fetch_time = now_taipei()
    except Exception as e:
        logger.error(f"啟動時初始化數據失敗: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    shutdown_scheduler()

@app.get("/", response_class=HTMLResponse)
async def get_index():
    index_path = os.path.join(TEMPLATES_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return HTMLResponse("<h3>index.html 尚未建立，請確認前端檔案已就緒。</h3>")

@app.get("/health")
async def health_check():
    """健康檢查端點，供 Keep-Alive 防休眠機制與外部 Cron 服務使用"""
    from scheduler import scheduler
    jobs = []
    if scheduler.running:
        jobs = [{"id": job.id, "name": job.name, "next_run": str(job.next_run_time)} for job in scheduler.get_jobs()]
    return {
        "status": "ok",
        "scheduler_running": scheduler.running,
        "jobs_count": len(jobs),
        "jobs": jobs,
        "timestamp": now_taipei().isoformat()
    }

@app.get("/api/status")
async def get_status():
    global last_fetch_time, cached_data
    now = now_taipei()
    
    # 若無快取或快取已過期，則重新獲取
    if not cached_data or not last_fetch_time or (now - last_fetch_time) > CACHE_DURATION:
        try:
            cached_data = run_tracking_cycle()
            last_fetch_time = now
        except Exception as e:
            logger.error(f"重新獲取資料失敗: {e}")
            if not cached_data:
                raise HTTPException(status_code=500, detail=str(e))
                
    return {
        "success": True,
        "last_update": last_fetch_time.isoformat() if last_fetch_time else None,
        "data": cached_data
    }

@app.post("/api/trigger")
async def trigger_monitoring():
    """手動強制觸發一次追蹤循環，並更新快取"""
    global last_fetch_time, cached_data
    try:
        cached_data = run_tracking_cycle()
        last_fetch_time = now_taipei()
        return {"success": True, "message": "已成功強制執行追蹤並更新數據", "data": cached_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"手動觸發失敗: {e}")

@app.post("/api/test_line")
async def test_line_notification(request: Request):
    """測試 LINE 推播通知"""
    try:
        body = await request.json()
        test_msg = body.get("message", "🔔 這是一條來自台灣大盤指數追蹤系統的測試通知！")
    except:
        test_msg = "🔔 這是一條來自台灣大盤指數追蹤系統的測試通知！"
        
    success = send_line_message(test_msg)
    if success:
        return {"success": True, "message": "測試通知已發送，請檢查您的 LINE。"}
    else:
        raise HTTPException(status_code=400, detail="發送失敗，請確認您的 LINE Token 與 User ID 是否設定正確。")

@app.get("/api/config")
async def get_config():
    """取得當前配置 (部分敏感資訊會遮蔽)"""
    config = get_env_config()
    
    # 遮蔽 Token
    token = config["LINE_CHANNEL_ACCESS_TOKEN"]
    masked_token = ""
    if token:
        masked_token = token[:6] + "..." + token[-6:] if len(token) > 12 else "已設定"
        
    return {
        "LINE_CHANNEL_ACCESS_TOKEN_MASKED": masked_token,
        "LINE_USER_ID": config["LINE_USER_ID"],
        "DROP_THRESHOLD": config["DROP_THRESHOLD"],
        "DROP_STEP": config["DROP_STEP"],
        "USE_KD_STRATEGY": config["USE_KD_STRATEGY"],
        "USE_BOLLINGER_STRATEGY": config["USE_BOLLINGER_STRATEGY"],
        "KD_LIMIT": config["KD_LIMIT"]
    }

@app.post("/api/config")
async def update_config(update_data: ConfigUpdate):
    """更新環境變數設定"""
    env_path = os.path.join(os.path.dirname(BASE_DIR), ".env") # 與 main.py 同層或上一層
    # 我們這裡專案在 tw_stock_tracker 目錄，所以 .env 會在該目錄下
    env_path = os.path.join(BASE_DIR, ".env")
    
    current_config = get_env_config()
    
    # 如果使用者傳入的 Token 為遮蔽格式或為空，表示不更新 Token
    token_to_save = update_data.LINE_CHANNEL_ACCESS_TOKEN
    if not token_to_save or token_to_save.endswith("..."):
        token_to_save = current_config["LINE_CHANNEL_ACCESS_TOKEN"]
        
    env_lines = [
        f"LINE_CHANNEL_ACCESS_TOKEN={token_to_save}",
        f"LINE_USER_ID={update_data.LINE_USER_ID}",
        f"DROP_THRESHOLD={update_data.DROP_THRESHOLD}",
        f"DROP_STEP={update_data.DROP_STEP}",
        f"USE_KD_STRATEGY={1 if update_data.USE_KD_STRATEGY else 0}",
        f"USE_BOLLINGER_STRATEGY={1 if update_data.USE_BOLLINGER_STRATEGY else 0}",
        f"KD_PERIOD=9",
        f"KD_LIMIT={update_data.KD_LIMIT}",
        f"BOLLINGER_PERIOD=20",
        f"BOLLINGER_STD_DEV=2.0"
    ]
    
    try:
        with open(env_path, "w", encoding="utf-8") as f:
            f.write("\n".join(env_lines) + "\n")
            
        # 重新載入環境變數
        import os
        from dotenv import load_dotenv
        load_dotenv(env_path, override=True)
        
        # 立即強制執行一次循環，將新參數套用
        global last_fetch_time, cached_data
        cached_data = run_tracking_cycle()
        last_fetch_time = now_taipei()
        
        return {"success": True, "message": "設定已成功儲存並生效"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"儲存設定檔失敗: {e}")

@app.get("/api/chart/{symbol}")
async def get_chart_data(symbol: str):
    """取得歷史日K線與指標數據以繪製圖表"""
    config = get_env_config()
    # 將 symbol 格式化
    yf_symbol = symbol
    if symbol == "TWII":
        yf_symbol = "^TWII"
    elif symbol in ETF_LIST:
        yf_symbol = ETF_LIST[symbol]["yf"]
        
    try:
        info = get_historical_and_indicators(yf_symbol, config)
        if not info or "df" not in info:
            raise HTTPException(status_code=404, detail="找不到對應的標的數據")
            
        df = info["df"].tail(40) # 只取最近 40 天畫圖，維持介面清晰度
        
        # 整理成 Chart.js 相容格式
        labels = [d.strftime("%m/%d") for d in df.index]
        close_prices = df["Close"].tolist()
        
        chart_data = {
            "labels": labels,
            "prices": close_prices,
        }
        
        if yf_symbol != "^TWII":
            chart_data.update({
                "K": df["K"].tolist(),
                "D": df["D"].tolist(),
                "MA": df["MA"].tolist(),
                "Upper": df["Upper"].tolist(),
                "Lower": df["Lower"].tolist(),
            })
            
        return {"success": True, "symbol": symbol, "data": chart_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"讀取圖表數據失敗: {e}")

if __name__ == "__main__":
    import uvicorn
    import os
    # 讀取雲端環境變數 PORT，若無則預設本地 8000 port
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
