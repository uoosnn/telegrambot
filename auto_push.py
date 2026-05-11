import time
import subprocess
import logging
import os
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

class AutoPushHandler(FileSystemEventHandler):
    def __init__(self):
        self.last_event_time = 0
        self.debounce_seconds = 5  # 5초 이내의 변경은 무시 (연속 저장 방지)
        
    def on_modified(self, event):
        # 디렉토리 변경이나 .git 폴더 내 변경은 무시
        if event.is_directory or '.git' in event.src_path or '__pycache__' in event.src_path:
            return
            
        current_time = time.time()
        if current_time - self.last_event_time > self.debounce_seconds:
            self.last_event_time = current_time
            logger.info(f"File modified: {event.src_path}. Starting auto commit/push...")
            
            # 약간 대기 (파일 쓰기가 완전히 끝날 시간을 벌어줌)
            time.sleep(1)
            self.commit_and_push()

    def commit_and_push(self):
        try:
            # 1. git add
            subprocess.run(['git', 'add', '.'], check=True, capture_output=True)
            
            # 2. git commit (변경사항이 없으면 예외 발생하므로 무시)
            status = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True)
            if not status.stdout.strip():
                logger.info("No changes to commit.")
                return
                
            subprocess.run(['git', 'commit', '-m', 'Auto commit: 코드가 수정되었습니다.'], check=True, capture_output=True)
            
            # 3. git push
            subprocess.run(['git', 'push', 'origin', 'main'], check=True, capture_output=True)
            
            logger.info("✅ Auto commit and push successful!")
        except subprocess.CalledProcessError as e:
            logger.error(f"❌ Git Command Error: {e.stderr.decode('utf-8', errors='ignore') if e.stderr else str(e)}")
        except Exception as e:
            logger.error(f"❌ Unexpected Error: {str(e)}")

def start_auto_push_watcher():
    path = os.path.dirname(os.path.abspath(__file__))
    event_handler = AutoPushHandler()
    observer = Observer()
    observer.schedule(event_handler, path, recursive=True)
    observer.start()
    logger.info("👀 Auto Push Watcher started! Watching for file changes...")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

if __name__ == "__main__":
    start_auto_push_watcher()
