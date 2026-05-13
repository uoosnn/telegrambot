import time
import subprocess
import logging
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

def check_and_push():
    """변경사항이 있으면 commit & push합니다."""
    repo_path = os.path.dirname(os.path.abspath(__file__))
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
        logger.error(f"❌ Git Error: {e.stderr.decode('utf-8', errors='ignore') if e.stderr else str(e)}")
    except Exception as e:
        logger.error(f"❌ Unexpected Error: {str(e)}")

def start_auto_push_watcher():
    """1시간 간격으로 변경사항을 확인하고 push합니다."""
    logger.info("👀 Auto Push Watcher started! Checking every 1 hour...")
    while True:
        check_and_push()
        time.sleep(3600)  # 1시간

if __name__ == "__main__":
    start_auto_push_watcher()
