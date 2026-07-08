from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from tracker import run_tracking_cycle
import logging

# 設定日誌
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("scheduler")

scheduler = BackgroundScheduler()

def start_scheduler():
    if not scheduler.running:
        # 1. 交易時間排程：週一至週五 09:00 至 13:59，每分鐘執行一次
        scheduler.add_job(
            func=run_tracking_cycle,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour="9-13",
                minute="*"
            ),
            id="trading_hours_job",
            name="台股交易時間每分鐘監控",
            replace_existing=True
        )
        
        # 2. 盤後最終確認排程：週一至週五 14:00 執行一次
        scheduler.add_job(
            func=run_tracking_cycle,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour=14,
                minute=0
            ),
            id="after_market_job",
            name="收盤盤後最終確認與分析",
            replace_existing=True
        )
        
        # 3. 晚間美股時段排程 (為 00646 追蹤美股)：週一至週五 22:00 執行一次
        scheduler.add_job(
            func=run_tracking_cycle,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour=22,
                minute=0
            ),
            id="night_market_job",
            name="晚間美股時段指標分析",
            replace_existing=True
        )
        
        scheduler.start()
        logger.info("APScheduler 已成功啟動，並已排定監控任務。")

def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown()
        logger.info("APScheduler 已關閉。")
