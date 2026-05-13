import os
import glob
import json
import logging
import asyncio
import schedule
import subprocess
import time
import threading
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

from ai_processor import AIProcessor
from github_uploader import GithubUploader
from news_scraper import NewsScraper
from auto_push import start_auto_push_watcher

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# 봇 시작 시간 기록
BOT_START_TIME = datetime.now(ZoneInfo("Asia/Seoul"))

# 봇이 보낸 뉴스 메시지 보관소 (Reply 시 사용)
pending_messages = {}
# 게시 확인 대기 중인 콘텐츠 보관소
pending_confirmations = {}

# Initialize Modules
try:
    ai_processor = AIProcessor()
except ValueError as e:
    logger.error(f"AI Processor init failed: {e}")
    ai_processor = None
    
try:
    github_uploader = GithubUploader()
except Exception as e:
    logger.error(f"Github Uploader init failed: {e}")
    github_uploader = None

try:
    news_scraper = NewsScraper()
except Exception as e:
    logger.error(f"News Scraper init failed: {e}")
    news_scraper = None



SENT_NEWS_FILE = "sent_news.json"

def load_sent_news():
    if os.path.exists(SENT_NEWS_FILE):
        try:
            with open(SENT_NEWS_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_sent_news(sent_urls):
    try:
        with open(SENT_NEWS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(sent_urls)[-1000:], f) # 최신 1000개만 유지
    except Exception:
        pass

async def send_news_task(bot_app):
    """뉴스를 스크래핑하고 텔레그램으로 전송합니다."""
    if not CHAT_ID or not news_scraper:
        return
        
    try:
        # 1. 게임 뉴스 가져오기
        game_news = news_scraper.fetch_game_news(limit=2)
        # 2. 트렌딩 뉴스 가져오기
        trending_news = news_scraper.fetch_trending_news(threshold=3)
        
        all_news = game_news + trending_news
        
        sent_urls = load_sent_news()
        new_news = [n for n in all_news if n['url'] not in sent_urls]
        
        if not new_news:
            logger.info("오늘 전송할 새로운 뉴스가 없습니다. (모두 이미 전송됨)")
            return

        for news in new_news:
            msg_text = f"📰 **새로운 뉴스 알림**\n\n"
            msg_text += f"**제목:** {news['title']}\n"
            if news.get('trend_keyword'):
                msg_text += f"🔥 **트렌드 키워드:** {news['trend_keyword']} (여러 기사 발생)\n"
            msg_text += f"\n🔗 [원문 보기]({news['url']})\n\n"
            msg_text += "💡 *이 메시지에 답장(Reply)으로 코멘트를 남기시면 AI가 블로그 글로 작성합니다.*"
            
            try:
                sent_msg = await bot_app.bot.send_message(chat_id=CHAT_ID, text=msg_text, parse_mode='Markdown')
                # 봇이 보낸 메시지 ID를 키로 하여 원본 포스트 데이터를 저장
                pending_messages[sent_msg.message_id] = {
                    'news_data': news
                }
                sent_urls.add(news['url'])
            except Exception as e:
                logger.error(f"Error sending telegram message: {e}")
                
        save_sent_news(sent_urls)
                
    except Exception as e:
        logger.error(f"Error fetching news: {e}")


def run_scheduler(loop, bot_app):
    def job():
        asyncio.run_coroutine_threadsafe(send_news_task(bot_app), loop)
        
    # 매일 아침 9시에 전송
    schedule.every().day.at("09:00").do(job)
    
    # [테스트용] 봇이 켜질 때 즉시 한번 전송합니다.
    job()
    
    while True:
        schedule.run_pending()
        time.sleep(1)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """봇 시작 및 초기화 명령어"""
    welcome_msg = (
        "안녕하세요! 저는 뉴스 알림 & AI 비서 봇입니다.\n"
        "매일 아침 9시에 핫이슈 뉴스와 게임 뉴스를 전달해 드립니다.\n\n"
        "**명령어 안내**\n"
        "/reset - 대화 내용을 초기화하고 새 대화를 시작합니다.\n"
        "/post [제목] - 지금까지의 1:1 대화를 바탕으로 블로그 포스트를 올립니다.\n"
        "*(뉴스를 블로그에 올리려면 봇이 보낸 뉴스 메시지에 답장(Reply)하세요)*"
    )
    if ai_processor:
        ai_processor.start_new_session()
    await update.message.reply_text(welcome_msg)


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """대화 세션 초기화 명령어"""
    if not ai_processor:
        await update.message.reply_text("❌ AI Processor가 설정되지 않았습니다.")
        return
    msg = ai_processor.start_new_session()
    await update.message.reply_text(msg)


async def post_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """현재까지의 대화를 기반으로 깃허브에 포스팅"""
    if not ai_processor or not github_uploader:
        await update.message.reply_text("❌ AI Processor 또는 Github Uploader가 설정되지 않았습니다.")
        return

    title_instruction = " ".join(context.args) if context.args else None
    
    if not title_instruction:
        await update.message.reply_text("⏳ 대화 내용을 분석하여 제목을 생성 중입니다...")
        try:
            title_instruction = ai_processor.generate_title_from_history()
        except Exception as e:
            logger.error(f"Error generating title: {e}")
            title_instruction = f"대화 기록 ({datetime.now().strftime('%Y-%m-%d')})"
    
    await update.message.reply_text(f"⏳ 대화를 정리하여 포스팅 중입니다... (주제: {title_instruction})")
    
    try:
        markdown_content = ai_processor.generate_blog_post_from_history(title_instruction)
        
        await update.message.reply_text("🌐 영문 및 일문으로 자동 번역을 수행 중입니다...")
        content_en = ai_processor.translate_blog_post(markdown_content, "English")
        content_ja = ai_processor.translate_blog_post(markdown_content, "Japanese")
        
        success, result = github_uploader.save_and_push(markdown_content, title=title_instruction, translations={'en': content_en, 'ja': content_ja})
        
        if success:
            await update.message.reply_text(f"✅ 블로그 포스팅 성공!\n\n저장 경로: {result}")
        else:
            await update.message.reply_text(f"❌ 깃허브 업로드 실패:\n{result}")
    except Exception as e:
        logger.error(f"Error during AI processing or GitHub upload: {e}")
        await update.message.reply_text(f"❌ 오류 발생: {str(e)}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """일반 텍스트 메시지 및 답장 처리"""
    user_text = update.message.text
    
    if not ai_processor:
        await update.message.reply_text("❌ AI API가 설정되지 않아 대화할 수 없습니다.")
        return
        
    # 1. 뉴스 메시지에 답장(Reply)한 경우인지 확인
    if update.message.reply_to_message:
        reply_to_id = update.message.reply_to_message.message_id
        
        if reply_to_id in pending_messages:
            # 정상적으로 pending_messages에 있는 뉴스에 Reply한 경우
            news_info = pending_messages[reply_to_id]['news_data']
            
            await update.message.reply_text("⏳ 코멘트를 확인했습니다. 뉴스를 분석하여 블로그 포스트를 작성 중입니다...")
            
            if not github_uploader:
                await update.message.reply_text("❌ Github Uploader가 설정되지 않았습니다.")
                return
                
            try:
                # 블로그 마크다운 생성
                markdown_content = ai_processor.generate_blog_from_news(news_info, user_text)
                
                # 미리보기를 보여주고 확인 버튼 제공
                preview = markdown_content[:500] + ("\n..." if len(markdown_content) > 500 else "")
                preview_msg = f"📝 **게시글 미리보기**\n\n{preview}\n\n---\n이 게시글을 블로그에 올릴까요?"
                
                keyboard = [
                    [
                        InlineKeyboardButton("✅ 게시", callback_data=f"confirm_post_{reply_to_id}"),
                        InlineKeyboardButton("❌ 취소", callback_data=f"cancel_post_{reply_to_id}")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                # 확인 대기 목록에 저장
                pending_confirmations[reply_to_id] = {
                    'markdown_content': markdown_content,
                    'news_info': news_info
                }
                
                await update.message.reply_text(preview_msg, reply_markup=reply_markup)
            except Exception as e:
                logger.error(f"Error generating news post: {e}")
                await update.message.reply_text(f"❌ 오류 발생: {str(e)}")
            return
        else:
            # pending_messages에 없는 경우: 봇 재시작 후 이전 뉴스에 Reply한 경우
            original_text = update.message.reply_to_message.text or ""
            if "📰" in original_text or "새로운 뉴스 알림" in original_text:
                await update.message.reply_text(
                    "⚠️ 봇이 재시작되어 해당 뉴스 데이터가 초기화되었습니다.\n"
                    "다음 뉴스 발송 때 다시 Reply해 주세요."
                )
                return

    # 2. 일반 대화인 경우
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    try:
        reply_text = ai_processor.send_message(user_text)
        await update.message.reply_text(reply_text)
    except Exception as e:
        logger.error(f"Error communicating with AI: {e}")
        await update.message.reply_text("❌ AI와 통신 중 오류가 발생했습니다.")


async def handle_confirmation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """게시 확인/취소 버튼 콜백 처리"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data.startswith("confirm_post_"):
        reply_to_id = int(data.replace("confirm_post_", ""))
        
        if reply_to_id not in pending_confirmations:
            await query.edit_message_text("⚠️ 해당 게시글 데이터를 찾을 수 없습니다. (이미 처리되었거나 만료됨)")
            return
        
        confirmation = pending_confirmations[reply_to_id]
        markdown_content = confirmation['markdown_content']
        
        if not github_uploader:
            await query.edit_message_text("❌ Github Uploader가 설정되지 않았습니다.")
            return
        
        await query.edit_message_text("🌐 영문 및 일문으로 자동 번역을 수행 중입니다...")
        
        try:
            content_en = ai_processor.translate_blog_post(markdown_content, "English")
            content_ja = ai_processor.translate_blog_post(markdown_content, "Japanese")
            
            success, result = github_uploader.save_and_push(
                markdown_content, 
                translations={'en': content_en, 'ja': content_ja}
            )
            
            if success:
                await query.edit_message_text(f"✅ 뉴스 기반 블로그 포스팅 성공!\n\n저장 경로: {result}")
                # 처리 완료 후 메모리에서 삭제
                pending_confirmations.pop(reply_to_id, None)
                pending_messages.pop(reply_to_id, None)
            else:
                await query.edit_message_text(f"❌ 깃허브 업로드 실패:\n{result}")
        except Exception as e:
            logger.error(f"Error posting confirmed news: {e}")
            await query.edit_message_text(f"❌ 오류 발생: {str(e)}")
    
    elif data.startswith("cancel_post_"):
        reply_to_id = int(data.replace("cancel_post_", ""))
        pending_confirmations.pop(reply_to_id, None)
        await query.edit_message_text("❌ 게시가 취소되었습니다.")


async def write_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """사용자가 입력한 마크다운을 그대로 블로그 포스트로 올립니다."""
    if not github_uploader:
        await update.message.reply_text("❌ Github Uploader가 설정되지 않았습니다.")
        return
        
    # 명령어(/write) 부분을 제외한 나머지 텍스트 추출
    user_text = update.message.text.replace("/write", "", 1).strip()
    
    if not user_text:
        await update.message.reply_text("❌ 작성할 내용(프론트매터 포함 마크다운)을 함께 적어주세요!\n예: `/write ---\ntitle: 제목\n...`")
        return
        
    await update.message.reply_text("📝 수동 포스트 업로드를 진행 중입니다...")
    
    # 정규식으로 title 추출 (예: title: "제목" 또는 title: 제목)
    title_match = re.search(r'title:\s*"?([^"\n]+)"?', user_text)
    if title_match:
        title = title_match.group(1).strip()
    else:
        title = f"직접 작성한 글 ({datetime.now().strftime('%Y-%m-%d %H%M%S')})"
        
    try:
        # 번역본 없이 바로 한국어 원본만 업로드 (translations 생략)
        success, result = github_uploader.save_and_push(user_text, title=title)
        
        if success:
            await update.message.reply_text(f"✅ 블로그 수동 포스팅 성공!\n\n저장 경로: {result}\n\n*※ 자동 번역이 생략되었습니다. 나중에 `/sync` 명령어를 통해 일괄 번역하실 수 있습니다.*")
        else:
            await update.message.reply_text(f"❌ 깃허브 업로드 실패:\n{result}")
    except Exception as e:
        logger.error(f"Error during manual posting: {e}")
        await update.message.reply_text(f"❌ 오류 발생: {str(e)}")


async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """사용 가능한 모든 명령어를 나열합니다."""
    msg = (
        "📋 **사용 가능한 명령어 목록**\n\n"
        "/start - 봇 시작 및 초기화\n"
        "/reset - 대화 내용 초기화 (새 대화 시작)\n"
        "/new - /reset과 동일\n"
        "/post [제목] - 대화 내용을 블로그에 포스팅 (제목 생략 시 AI가 자동 생성)\n"
        "/write - 마크다운을 직접 입력하여 포스팅\n"
        "/sync - 블로그 폴더 스캔 후 미번역 포스트 일괄 번역\n"
        "/usage - API 사용량 및 예상 요금 확인\n"
        "/uptime - 봇 가동 시간 확인\n"
        "/info - 이 명령어 목록 표시\n\n"
        "💡 *봇이 보낸 뉴스에 답장(Reply)하면 AI가 게시글을 작성하고 확인을 요청합니다.*"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')


async def uptime_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """봇이 켜져있던 시간을 출력합니다."""
    kst_now = datetime.now(ZoneInfo("Asia/Seoul"))
    uptime_delta = kst_now - BOT_START_TIME
    
    total_seconds = int(uptime_delta.total_seconds())
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    
    uptime_parts = []
    if days > 0:
        uptime_parts.append(f"{days}일")
    if hours > 0:
        uptime_parts.append(f"{hours}시간")
    if minutes > 0:
        uptime_parts.append(f"{minutes}분")
    uptime_parts.append(f"{seconds}초")
    uptime_str = " ".join(uptime_parts)
    
    msg = (
        f"⏱ **봇 가동 정보**\n\n"
        f"🟢 시작 시간: {BOT_START_TIME.strftime('%Y-%m-%d %H:%M:%S')} (KST)\n"
        f"⏳ 가동 시간: {uptime_str}"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')


async def usage_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """현재까지 사용한 API 토큰 및 예상 요금 출력"""
    if not ai_processor:
        await update.message.reply_text("❌ AI Processor가 설정되지 않았습니다.")
        return
        
    stats = ai_processor.get_usage_stats()
    in_tokens = stats.get("input_tokens", 0)
    out_tokens = stats.get("output_tokens", 0)
    
    # Gemini Pro Pay-as-you-go pricing (approx):
    # Input: $1.25 / 1M tokens
    # Output: $5.00 / 1M tokens
    cost = (in_tokens / 1_000_000) * 1.25 + (out_tokens / 1_000_000) * 5.00
    
    msg = (
        f"📊 **현재까지 봇 누적 API 사용량**\n\n"
        f"🔹 입력 토큰: {in_tokens:,} 개\n"
        f"🔸 출력 토큰: {out_tokens:,} 개\n\n"
        f"💸 **예상 지출 요금:** 약 ${cost:.4f}\n"
        f"*(※ 이 요금은 자체 기록한 토큰을 바탕으로 한 단순 추산치이며, 구글 클라우드의 실제 청구액과 다를 수 있습니다.)*"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')


async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """로컬 블로그 폴더를 스캔하여 번역이 안 된 글을 번역하고 푸시합니다."""
    await update.message.reply_text("🔄 블로그 폴더 스캔을 시작합니다...")
    
    if not ai_processor or not github_uploader:
        await update.message.reply_text("❌ AI Processor 또는 Github Uploader가 설정되지 않았습니다.")
        return
        
    blog_dir = "/source/uoosnn.github.io/blog"
    en_dir = "/source/uoosnn.github.io/en/blog"
    ja_dir = "/source/uoosnn.github.io/ja/blog"
    
    os.makedirs(en_dir, exist_ok=True)
    os.makedirs(ja_dir, exist_ok=True)
    
    md_files = glob.glob(os.path.join(blog_dir, "*.md"))
    needs_sync = []
    
    for file_path in md_files:
        filename = os.path.basename(file_path)
        if filename == "index.md":
            continue
            
        kr_mtime = os.path.getmtime(file_path)
        
        en_path = os.path.join(en_dir, filename)
        ja_path = os.path.join(ja_dir, filename)
        
        # 번역본이 없거나 한국어 원본보다 오래된 경우 (여유 2초)
        is_en_outdated = not os.path.exists(en_path) or (kr_mtime > os.path.getmtime(en_path) + 2)
        is_ja_outdated = not os.path.exists(ja_path) or (kr_mtime > os.path.getmtime(ja_path) + 2)
        
        if is_en_outdated or is_ja_outdated:
            needs_sync.append((file_path, filename))
            
    if not needs_sync:
        await update.message.reply_text("✅ 모든 포스트가 이미 동기화(번역)되어 있습니다.")
        return
        
    await update.message.reply_text(f"📝 총 {len(needs_sync)}개의 문서가 발견되었습니다. 번역 및 동기화를 진행합니다. 잠시만 기다려주세요...")
    
    for file_path, filename in needs_sync:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            content_en = ai_processor.translate_blog_post(content, "English")
            content_ja = ai_processor.translate_blog_post(content, "Japanese")
            
            en_path = os.path.join(en_dir, filename)
            ja_path = os.path.join(ja_dir, filename)
            
            with open(en_path, "w", encoding="utf-8") as f:
                f.write(content_en)
                
            with open(ja_path, "w", encoding="utf-8") as f:
                f.write(content_ja)
                
        except Exception as e:
            logger.error(f"Error syncing {filename}: {e}")
            await update.message.reply_text(f"❌ {filename} 동기화 중 오류 발생: {e}")
            return
            
    # Commit and Push
    try:

        subprocess.run(["git", "add", "."], cwd="/source/uoosnn.github.io", check=True)
        # 변경사항이 없을 수도 있으므로 (git commit은 에러가 날 수 있음)
        result = subprocess.run(["git", "status", "--porcelain"], cwd="/source/uoosnn.github.io", capture_output=True, text=True)
        if result.stdout.strip():
            subprocess.run(["git", "commit", "-m", "docs: Auto translate manual post via /sync"], cwd="/source/uoosnn.github.io", check=True)
            subprocess.run(["git", "push"], cwd="/source/uoosnn.github.io", check=True)
            await update.message.reply_text(f"✅ 총 {len(needs_sync)}개의 문서 다국어 번역 및 배포가 완료되었습니다!")
        else:
            await update.message.reply_text("✅ 깃허브에 반영할 새로운 변경사항이 없습니다.")
    except Exception as e:
        logger.error(f"Error pushing synced files: {e}")
        await update.message.reply_text(f"❌ 깃허브 업로드 실패: {str(e)}")


def main():
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set in .env")
        return

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("reset", reset_command))
    application.add_handler(CommandHandler("new", reset_command))
    application.add_handler(CommandHandler("post", post_command))
    application.add_handler(CommandHandler("write", write_command))
    application.add_handler(CommandHandler("usage", usage_command))
    application.add_handler(CommandHandler("sync", sync_command))
    application.add_handler(CommandHandler("info", info_command))
    application.add_handler(CommandHandler("uptime", uptime_command))
    application.add_handler(CallbackQueryHandler(handle_confirmation_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # 스케줄러를 백그라운드 스레드에서 실행
    loop = asyncio.get_event_loop()
    threading.Thread(target=run_scheduler, args=(loop, application), daemon=True).start()

    # 자동 커밋/푸시 감시자를 백그라운드 스레드에서 실행
    threading.Thread(target=start_auto_push_watcher, daemon=True).start()

    logger.info("Bot is running...")
    application.run_polling()

if __name__ == '__main__':
    main()
