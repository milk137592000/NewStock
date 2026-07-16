from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from tracker import run_tracking_cycle
import requests
import os
import logging
import pytz

# 設定日誌
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("scheduler")

# 台北時區
TZ_TAIPEI = pytz.timezone("Asia/Taipei")

scheduler = BackgroundScheduler(timezone=TZ_TAIPEI)

def keep_alive():
    """定時 Ping 自己的 /health 端點，防止 Render 免費版因無流量而休眠"""
    app_url = os.environ.get("RENDER_EXTERNAL_URL", "")
    if not app_url:
        app_url = os.environ.get("APP_URL", "")
    
    if app_url:
        try:
            response = requests.get(f"{app_url}/health", timeout=10)
            logger.info(f"[Keep-Alive] Ping {app_url}/health -> {response.status_code}")
        except Exception as e:
            logger.warning(f"[Keep-Alive] Ping 失敗: {e}")
    else:
        logger.debug("[Keep-Alive] 未設定 APP_URL 或 RENDER_EXTERNAL_URL，跳過自我喚醒。")

def start_scheduler():
    if not scheduler.running:
        # 1. 交易時間排程：週一至週五 09:00 至 13:59 (台北時間)，每分鐘執行一次
        scheduler.add_job(
            func=run_tracking_cycle,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour="9-13",
                minute="*",
                timezone=TZ_TAIPEI
            ),
            id="trading_hours_job",
            name="台股交易時間每分鐘監控",
            replace_existing=True
        )
        
        # 2. 盤後最終確認排程：週一至週五 14:00 (台北時間) 執行一次
        scheduler.add_job(
            func=run_tracking_cycle,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour=14,
                minute=0,
                timezone=TZ_TAIPEI
            ),
            id="after_market_job",
            name="收盤盤後最終確認與分析",
            replace_existing=True
        )
        
        # 3. 晚間美股時段排程 (為 00646 追蹤美股)：週一至週五 22:00 (台北時間) 執行一次
        scheduler.add_job(
            func=run_tracking_cycle,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour=22,
                minute=0,
                timezone=TZ_TAIPEI
            ),
            id="night_market_job",
            name="晚間美股時段指標分析",
            replace_existing=True
        )
        
        # 4. Keep-Alive 防休眠排程：每 10 分鐘 Ping 自己一次，全天候運作
        scheduler.add_job(
            func=keep_alive,
            trigger=IntervalTrigger(minutes=10),
            id="keep_alive_job",
            name="防休眠自我喚醒 (每10分鐘)",
            replace_existing=True
        )
        
        scheduler.start()
        logger.info("APScheduler 已成功啟動，並已排定監控任務（含防休眠機制）。")
        logger.info(f"Scheduler timezone: {scheduler.timezone}")
        for job in scheduler.get_jobs():
            logger.info(f"  Job '{job.name}' next_run: {job.next_run_time}")

def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown()
        logger.info("APScheduler 已關閉。")
