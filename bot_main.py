import os
import logging
import asyncio
import schedule
import time
import threading
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

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

# 봇이 보낸 뉴스 메시지 보관소 (Reply 시 사용)
pending_messages = {}

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

import json

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

    title_instruction = " ".join(context.args) if context.args else "대화 요약"
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
            news_info = pending_messages[reply_to_id]['news_data']
            
            await update.message.reply_text("⏳ 코멘트를 확인했습니다. 뉴스를 분석하여 블로그 포스트를 작성 중입니다...")
            
            if not github_uploader:
                await update.message.reply_text("❌ Github Uploader가 설정되지 않았습니다.")
                return
                
            try:
                # 블로그 마크다운 생성 (게시된 시간 포함)
                markdown_content = ai_processor.generate_blog_from_news(news_info, user_text)
                
                await update.message.reply_text("🌐 영문 및 일문으로 자동 번역을 수행 중입니다...")
                content_en = ai_processor.translate_blog_post(markdown_content, "English")
                content_ja = ai_processor.translate_blog_post(markdown_content, "Japanese")
                
                # 깃허브에 업로드
                success, result = github_uploader.save_and_push(markdown_content, translations={'en': content_en, 'ja': content_ja})
                
                if success:
                    await update.message.reply_text(f"✅ 뉴스 기반 블로그 자동 포스팅 성공!\n\n저장 경로: {result}")
                    del pending_messages[reply_to_id] # 처리 완료 후 메모리에서 삭제
                else:
                    await update.message.reply_text(f"❌ 깃허브 업로드 실패:\n{result}")
            except Exception as e:
                logger.error(f"Error posting news: {e}")
                await update.message.reply_text(f"❌ 오류 발생: {str(e)}")
            return

    # 2. 일반 대화인 경우
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    try:
        reply_text = ai_processor.send_message(user_text)
        await update.message.reply_text(reply_text)
    except Exception as e:
        logger.error(f"Error communicating with AI: {e}")
        await update.message.reply_text("❌ AI와 통신 중 오류가 발생했습니다.")


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
    application.add_handler(CommandHandler("usage", usage_command))
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
