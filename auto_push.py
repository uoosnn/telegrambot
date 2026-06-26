import time
import subprocess
import logging
import os
import asyncio

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

def check_and_push(bot=None, chat_id=None):
    """변경사항이 있으면 commit & push합니다."""
    # uoosnn.github.io 디렉토리를 바라보도록 수정. (bot_main.py의 github_uploader 타겟)
    repo_path = os.getenv("TARGET_REPO_PATH", "/source/uoosnn.github.io")
    try:
        subprocess.run(['git', 'add', '.'], cwd=repo_path, check=True, capture_output=True)
        status = subprocess.run(['git', 'status', '--porcelain'],
                                cwd=repo_path, capture_output=True, text=True)
        if not status.stdout.strip():
            return  # 변경 없으면 로그 출력 안 함

        subprocess.run(['git', 'commit', '-m', 'Auto commit: 코드가 수정되었습니다.'],
                        cwd=repo_path, check=True, capture_output=True)
        subprocess.run(['git', 'push', 'origin', 'main'],
                        cwd=repo_path, check=True, capture_output=True)
        logger.info("✅ Auto commit and push successful!")
    except subprocess.CalledProcessError as e:
        error_msg = f"❌ Git Error: {e.stderr.decode('utf-8', errors='ignore') if e.stderr else str(e)}"
        logger.error(error_msg)
        if bot and chat_id:
            try:
                # 백그라운드 스레드에서 async 함수 호출
                asyncio.run(bot.send_message(chat_id=chat_id, text=error_msg))
            except Exception as inner_e:
                logger.error(f"Failed to send telegram alert: {inner_e}")
    except Exception as e:
        error_msg = f"❌ Unexpected Error: {str(e)}"
        logger.error(error_msg)
        if bot and chat_id:
            try:
                asyncio.run(bot.send_message(chat_id=chat_id, text=error_msg))
            except Exception as inner_e:
                logger.error(f"Failed to send telegram alert: {inner_e}")

def start_auto_push_watcher(bot=None, chat_id=None):
    """1시간 간격으로 변경사항을 확인하고 push합니다."""
    logger.info("👀 Auto Push Watcher started! Checking every 1 hour...")
    while True:
        check_and_push(bot, chat_id)
        time.sleep(3600)  # 1시간

if __name__ == "__main__":
    start_auto_push_watcher()
