import os
import json
import base64
import logging
import asyncio
import uuid
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from dynamo_manager import DynamoManager
from ai_processor import AIProcessor
from github_uploader import GithubUploader
from news_scraper import NewsScraper

load_dotenv()

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

dynamo_mgr = DynamoManager()
ai_processor = AIProcessor(dynamo_mgr=dynamo_mgr)
github_uploader = GithubUploader()
news_scraper = NewsScraper()


# --- Helper: Split Message Sending ---
async def send_split_message(bot: Bot, chat_id: int, text: str, parse_mode=None, reply_markup=None):
    if not text:
        return
    # 4000자 단위 분할
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for idx, chunk in enumerate(chunks):
        markup = reply_markup if idx == len(chunks) - 1 else None
        try:
            await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=parse_mode, reply_markup=markup)
        except Exception as e:
            # parse_mode 에러(마크다운 문법 불일치) 발생 시 일반 텍스트로 fallback
            logger.warning(f"Failed to send with parse_mode {parse_mode}: {e}. Retrying plain text.")
            await bot.send_message(chat_id=chat_id, text=chunk, reply_markup=markup)


# --- 1. Scheduled Task: News Scraper (EventBridge) ---

async def handle_scheduled_news(bot: Bot):
    if not CHAT_ID or not news_scraper:
        logger.warning("CHAT_ID or news_scraper is not configured.")
        return {"status": "skipped", "reason": "missing config"}

    logger.info("Starting scheduled news scrape...")
    try:
        game_news = news_scraper.fetch_game_news(limit=1)
        trending_news = news_scraper.fetch_trending_news(limit=2, threshold=2)
        japan_news = news_scraper.fetch_japan_news(limit=2)

        all_news = game_news + trending_news + japan_news
        sent_urls = dynamo_mgr.get_sent_news()
        new_news = [n for n in all_news if n.get('url') not in sent_urls]

        if not new_news:
            logger.info("No new news to send.")
            return {"status": "ok", "message": "no new news"}

        newly_sent_urls = []
        for news in new_news:
            msg_text = f"📰 **새로운 뉴스 알림**\n\n"
            msg_text += f"**제목:** {news['title']}\n"
            if news.get('trend_keyword'):
                msg_text += f"🔥 **트렌드 키워드:** {news['trend_keyword']} (여러 기사 발생)\n"
            msg_text += f"\n🔗 [원문 보기]({news['url']})\n\n"
            msg_text += "💡 *이 메시지에 답장(Reply)으로 코멘트를 남기시면 AI가 블로그 글로 작성합니다.*"

            try:
                sent_msg = await bot.send_message(chat_id=CHAT_ID, text=msg_text, parse_mode='Markdown')
                dynamo_mgr.save_pending_message(sent_msg.message_id, news)
                newly_sent_urls.append(news['url'])
            except Exception as e:
                logger.error(f"Error sending news message: {e}")

        if newly_sent_urls:
            dynamo_mgr.add_sent_news(newly_sent_urls)

        return {"status": "ok", "sent_count": len(newly_sent_urls)}
    except Exception as e:
        logger.error(f"Error in handle_scheduled_news: {e}")
        return {"status": "error", "error": str(e)}


# --- 2. Direct Dispatch Handlers ---

async def handle_start_cmd(bot: Bot, chat_id: int):
    welcome_msg = (
        "안녕하세요! 저는 뉴스 알림 & AI 비서 봇 (서버리스 버전)입니다.\n"
        "매일 아침 9시에 핫이슈 뉴스와 게임 뉴스를 전달해 드립니다.\n\n"
        "**명령어 안내**\n"
        "/reset - 대화 내용을 초기화하고 새 대화를 시작합니다.\n"
        "/post [제목] - 대화를 블로그(blog)에 포스팅\n"
        "/techpost [제목] - 기술 블로그(tech)에 포스팅\n"
        "/draft [제목/내용] - 검색엔진/목록에 숨겨진 초안(draft: true)으로 저장\n"
        "/unpublish [키워드] - 기존 발행 글을 비공개(초안)로 전환\n"
        "/delete [키워드] - 기존 글을 3개 언어(KO/EN/JA)에서 영구 삭제\n"
        "/write - 마크다운 직접 입력 포스팅\n"
        "/usage - API 토큰 사용량 확인\n"
        "/info - 전체 명령어 목록\n"
        "*(뉴스를 블로그에 올리려면 봇이 보낸 뉴스 메시지에 답장(Reply)하세요)*"
    )
    ai_processor.start_new_session(chat_id)
    await bot.send_message(chat_id=chat_id, text=welcome_msg)

async def handle_reset_cmd(bot: Bot, chat_id: int):
    msg = ai_processor.start_new_session(chat_id)
    await bot.send_message(chat_id=chat_id, text=msg)

async def handle_usage_cmd(bot: Bot, chat_id: int):
    stats = ai_processor.get_usage_stats()
    in_tokens = stats.get("input_tokens", 0)
    out_tokens = stats.get("output_tokens", 0)
    cost = (in_tokens / 1_000_000) * 1.25 + (out_tokens / 1_000_000) * 5.00

    msg = (
        f"📊 **현재까지 봇 누적 API 사용량**\n\n"
        f"🔹 입력 토큰: {in_tokens:,} 개\n"
        f"🔸 출력 토큰: {out_tokens:,} 개\n\n"
        f"💸 **예상 지출 요금:** 약 ${cost:.4f}\n"
        f"*(※ 자체 기록 토큰 기반 단순 추산치)*"
    )
    await bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')

async def handle_info_cmd(bot: Bot, chat_id: int):
    msg = (
        "📋 **사용 가능한 명령어 목록 (서버리스)**\n\n"
        "/start - 봇 시작 및 초기화\n"
        "/reset - 대화 내용 초기화 (새 대화 시작)\n"
        "/post [제목] - 대화 내용을 블로그(blog)에 포스팅\n"
        "/techpost [제목] - 대화 내용을 기술 블로그(tech)에 포스팅\n"
        "/draft [제목] - 검색엔진에 노출되지 않는 비공개 초안(draft: true)으로 저장\n"
        "/unpublish [키워드] - 기존 발행된 글을 비공개(초안)로 전환\n"
        "/delete [키워드] - 글을 3개 국어(KO/EN/JA)에서 영구 삭제\n"
        "/write - 마크다운 직접 입력 포스팅\n"
        "/usage - API 사용량 및 예상 요금 확인\n"
        "/info - 이 명령어 목록 표시\n\n"
        "💡 *봇이 보낸 뉴스에 답장(Reply)하면 AI가 게시글을 작성하고 확인을 요청합니다.*"
    )
    await bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')

async def handle_draft_cmd(bot: Bot, chat_id: int, title_arg: str):
    title_instruction = title_arg.strip() if title_arg else None
    if not title_instruction:
        try:
            title_instruction = await asyncio.to_thread(ai_processor.generate_title_from_history, chat_id)
        except Exception:
            title_instruction = f"초안 노트 ({datetime.now().strftime('%Y-%m-%d')})"

    await bot.send_message(chat_id=chat_id, text=f"📝 검색엔진 및 목록에서 제외되는 비공개 초안(draft: true)으로 저장 중입니다... (제목: {title_instruction})")
    try:
        markdown_content = await asyncio.to_thread(ai_processor.generate_blog_post_from_history, title_instruction, chat_id)
        success, result = await asyncio.to_thread(
            github_uploader.save_and_push_draft,
            markdown_content,
            title=title_instruction,
            folder_name="blog"
        )
        if success:
            await bot.send_message(chat_id=chat_id, text=f"✅ 비공개 초안 저장 성공!\n\n저장 경로: {result}\n*(※ draft: true가 설정되어 사이트 목록 및 Google 검색엔진 색인에서 자동 제외됩니다)*")
        else:
            await bot.send_message(chat_id=chat_id, text=f"❌ 깃허브 업로드 실패:\n{result}")
    except Exception as e:
        logger.error(f"Error in handle_draft_cmd: {e}", exc_info=True)
        await bot.send_message(chat_id=chat_id, text=f"❌ 오류 발생: {str(e)}")

async def handle_unpublish_cmd(bot: Bot, chat_id: int, keyword: str):
    if not keyword.strip():
        await bot.send_message(chat_id=chat_id, text="❌ 비공개로 전환할 글 제목이나 키워드를 입력해 주세요.\n예: `/unpublish AWS 백업`")
        return

    await bot.send_message(chat_id=chat_id, text=f"⏳ '{keyword}' 글을 검색하여 비공개(draft: true)로 전환 중입니다...")
    try:
        success, result = await asyncio.to_thread(github_uploader.unpublish_post, keyword.strip())
        if success:
            await bot.send_message(chat_id=chat_id, text=f"✅ {result}\n*(※ 사이트 목록에서 숨겨지고 Google noindex가 적용됩니다)*")
        else:
            await bot.send_message(chat_id=chat_id, text=f"⚠️ {result}")
    except Exception as e:
        logger.error(f"Error in handle_unpublish_cmd: {e}", exc_info=True)
        await bot.send_message(chat_id=chat_id, text=f"❌ 오류 발생: {str(e)}")

async def handle_delete_cmd(bot: Bot, chat_id: int, keyword: str):
    if not keyword.strip():
        await bot.send_message(chat_id=chat_id, text="❌ 삭제할 글 제목이나 키워드를 입력해 주세요.\n예: `/delete 삭제할글제목`")
        return

    await bot.send_message(chat_id=chat_id, text=f"⏳ '{keyword}' 글을 3개 언어에서 영구 삭제 중입니다...")
    try:
        success, result = await asyncio.to_thread(github_uploader.delete_post, keyword.strip())
        if success:
            await bot.send_message(chat_id=chat_id, text=f"✅ {result}")
        else:
            await bot.send_message(chat_id=chat_id, text=f"⚠️ {result}")
    except Exception as e:
        logger.error(f"Error in handle_delete_cmd: {e}", exc_info=True)
        await bot.send_message(chat_id=chat_id, text=f"❌ 오류 발생: {str(e)}")

async def handle_post_cmd(bot: Bot, chat_id: int, title_arg: str, folder_name="blog"):
    target_name = "기술 블로그(tech)" if folder_name == "tech" else "블로그(blog)"
    title_instruction = title_arg.strip() if title_arg else None

    if not title_instruction:
        await bot.send_message(chat_id=chat_id, text="⏳ 대화 내용을 분석하여 제목을 생성 중입니다...")
        try:
            title_instruction = await asyncio.to_thread(ai_processor.generate_title_from_history, chat_id)
        except Exception as e:
            logger.error(f"Error generating title: {e}")
            title_instruction = f"대화 기록 ({datetime.now().strftime('%Y-%m-%d')})"

    await bot.send_message(chat_id=chat_id, text=f"⏳ 대화를 정리하여 {target_name}에 포스팅 중입니다... (주제: {title_instruction})")

    try:
        markdown_content = await asyncio.to_thread(ai_processor.generate_blog_post_from_history, title_instruction, chat_id)
        if folder_name == "tech":
            markdown_content = markdown_content.replace("tags: [AI, 대화요약]", "tags: [Tech, AI, 기술분석]")

        await bot.send_message(chat_id=chat_id, text="🌐 영문 및 일문으로 자동 번역을 동시에 수행 중입니다...")
        content_en, content_ja = await asyncio.gather(
            asyncio.to_thread(ai_processor.translate_blog_post, markdown_content, "English"),
            asyncio.to_thread(ai_processor.translate_blog_post, markdown_content, "Japanese")
        )

        success, result = await asyncio.to_thread(
            github_uploader.save_and_push,
            markdown_content,
            title=title_instruction,
            folder_name=folder_name,
            translations={'en': content_en, 'ja': content_ja}
        )


        if success:
            await bot.send_message(chat_id=chat_id, text=f"✅ {target_name} 포스팅 성공!\n\n저장 경로: {result}")
        else:
            await bot.send_message(chat_id=chat_id, text=f"❌ 깃허브 업로드 실패:\n{result}")
    except Exception as e:
        logger.error(f"Error during post: {e}", exc_info=True)
        await bot.send_message(chat_id=chat_id, text=f"❌ 오류 발생: {str(e)}")

async def handle_write_cmd(bot: Bot, chat_id: int, user_text: str):
    if not user_text:
        await bot.send_message(chat_id=chat_id, text="❌ 작성할 내용(프론트매터 포함 마크다운)을 함께 적어주세요!\n예: `/write ---\ntitle: 제목\n...`")
        return

    await bot.send_message(chat_id=chat_id, text="📝 수동 포스트 업로드를 진행 중입니다...")
    title_match = re.search(r'title:\s*"?([^"\n]+)"?', user_text)
    title = title_match.group(1).strip() if title_match else f"직접 작성한 글 ({datetime.now().strftime('%Y-%m-%d %H%M%S')})"

    try:
        success, result = await asyncio.to_thread(github_uploader.save_and_push, user_text, title=title)
        if success:
            await bot.send_message(chat_id=chat_id, text=f"✅ 블로그 수동 포스팅 성공!\n\n저장 경로: {result}")
        else:
            await bot.send_message(chat_id=chat_id, text=f"❌ 깃허브 업로드 실패:\n{result}")
    except Exception as e:
        logger.error(f"Error during write: {e}", exc_info=True)
        await bot.send_message(chat_id=chat_id, text=f"❌ 오류 발생: {str(e)}")

async def handle_text_message(bot: Bot, chat_id: int, user_text: str, reply_to_msg: dict = None):
    # 1. 뉴스 메시지에 답장(Reply)한 경우
    if reply_to_msg:
        reply_to_id = reply_to_msg.get("message_id")
        news_info = dynamo_mgr.get_pending_message(reply_to_id)

        if news_info:
            await bot.send_message(chat_id=chat_id, text="⏳ 코멘트를 확인했습니다. 뉴스를 분석하여 블로그 포스트를 작성 중입니다...")
            try:
                markdown_content = await asyncio.to_thread(ai_processor.generate_blog_from_news, news_info, user_text)
                preview = markdown_content[:500] + ("\n..." if len(markdown_content) > 500 else "")
                preview_msg = f"📝 **게시글 미리보기**\n\n{preview}\n\n---\n이 게시글을 블로그에 올릴까요?"

                keyboard = [
                    [
                        InlineKeyboardButton("✅ 게시", callback_data=f"confirm_post_{reply_to_id}"),
                        InlineKeyboardButton("❌ 취소", callback_data=f"cancel_post_{reply_to_id}")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                dynamo_mgr.save_pending_confirmation(reply_to_id, markdown_content, news_info)
                await bot.send_message(chat_id=chat_id, text=preview_msg, reply_markup=reply_markup)
            except Exception as e:
                logger.error(f"Error generating news post: {e}", exc_info=True)
                await bot.send_message(chat_id=chat_id, text=f"❌ 오류 발생: {str(e)}")
            return
        else:
            orig_text = reply_to_msg.get("text", "")
            if "📰" in orig_text or "새로운 뉴스 알림" in orig_text:
                await bot.send_message(chat_id=chat_id, text="⚠️ 해당 뉴스 데이터가 만료되었습니다. 다음 뉴스에 답장해 주세요.")
                return

    # 2. 일반 AI 대화
    await bot.send_chat_action(chat_id=chat_id, action='typing')
    try:
        reply_text = await asyncio.to_thread(ai_processor.send_message, user_text, chat_id)
        if not reply_text:
            reply_text = "네, 메시지를 확인했습니다."
        await send_split_message(bot, chat_id, reply_text)
    except Exception as e:
        logger.error(f"Error in handle_text_message: {e}", exc_info=True)
        await bot.send_message(chat_id=chat_id, text=f"❌ AI 오류: {str(e)}")

async def handle_photo_message(bot: Bot, chat_id: int, photos: list, caption: str):
    await bot.send_message(chat_id=chat_id, text="⏳ 이미지를 깃허브에 업로드 중입니다...")
    try:
        # 가장 해상도가 높은 마지막 사진 가져오기
        photo = photos[-1]
        file_id = photo.get("file_id")
        file_obj = await bot.get_file(file_id)
        file_bytes = await file_obj.download_as_bytearray()

        filename = f"img_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.jpg"
        success, result = await asyncio.to_thread(github_uploader.save_image_and_push, file_bytes, filename)

        if success:
            context_msg = f"사용자가 이미지를 업로드했습니다. 마크다운 링크: {result}"
            if caption:
                context_msg += f"\n사진 설명(캡션): {caption}"

            await asyncio.to_thread(ai_processor.send_message, context_msg, chat_id)
            await bot.send_message(
                chat_id=chat_id,
                text=f"✅ 이미지 업로드 완료!\n\nAI가 이 이미지를 인지했습니다. 바로 사용할 수 있는 마크다운 링크입니다:\n`{result}`",
                parse_mode='Markdown'
            )
        else:
            await bot.send_message(chat_id=chat_id, text=f"❌ 깃허브 업로드 실패:\n{result}")
    except Exception as e:
        logger.error(f"Error uploading photo: {e}", exc_info=True)
        await bot.send_message(chat_id=chat_id, text=f"❌ 오류 발생: {str(e)}")

async def handle_callback_query(bot: Bot, callback_query: dict):
    cb_id = callback_query.get("id")
    data = callback_query.get("data", "")
    message = callback_query.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    msg_id = message.get("message_id")

    try:
        await bot.answer_callback_query(callback_query_id=cb_id)
    except Exception:
        pass

    if data.startswith("confirm_post_"):
        reply_to_id = int(data.replace("confirm_post_", ""))
        confirmation = dynamo_mgr.get_pending_confirmation(reply_to_id)

        if not confirmation:
            await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="⚠️ 해당 게시글 데이터를 찾을 수 없습니다. (만료되었거나 이미 처리됨)")
            return

        markdown_content = confirmation['markdown_content']
        await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="🌐 영문 및 일문으로 자동 번역을 동시에 수행 중입니다...")

        try:
            content_en, content_ja = await asyncio.gather(
                asyncio.to_thread(ai_processor.translate_blog_post, markdown_content, "English"),
                asyncio.to_thread(ai_processor.translate_blog_post, markdown_content, "Japanese")
            )

            success, result = await asyncio.to_thread(
                github_uploader.save_and_push,
                markdown_content,
                translations={'en': content_en, 'ja': content_ja}
            )


            if success:
                await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=f"✅ 뉴스 기반 블로그 포스팅 성공!\n\n저장 경로: {result}")
                dynamo_mgr.delete_pending_confirmation(reply_to_id)
                dynamo_mgr.delete_pending_message(reply_to_id)
            else:
                await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=f"❌ 깃허브 업로드 실패:\n{result}")
        except Exception as e:
            logger.error(f"Error posting confirmed news: {e}", exc_info=True)
            await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=f"❌ 오류 발생: {str(e)}")

    elif data.startswith("cancel_post_"):
        reply_to_id = int(data.replace("cancel_post_", ""))
        dynamo_mgr.delete_pending_confirmation(reply_to_id)
        await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="❌ 게시가 취소되었습니다.")


# --- 3. Unified Update Dispatcher ---

async def dispatch_telegram_update(bot: Bot, update_data: dict):
    # 1. Callback Query (Inline Button Click)
    if "callback_query" in update_data:
        await handle_callback_query(bot, update_data["callback_query"])
        return

    # 2. Message
    message = update_data.get("message") or update_data.get("edited_message")
    if not message:
        return

    chat_id = message.get("chat", {}).get("id")
    if not chat_id:
        return

    text = (message.get("text") or "").strip()
    photos = message.get("photo")
    caption = message.get("caption", "")
    reply_to = message.get("reply_to_message")

    # Photo
    if photos:
        await handle_photo_message(bot, chat_id, photos, caption)
        return

    # Commands
    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if cmd in ["/start"]:
            await handle_start_cmd(bot, chat_id)
        elif cmd in ["/reset", "/new"]:
            await handle_reset_cmd(bot, chat_id)
        elif cmd in ["/usage"]:
            await handle_usage_cmd(bot, chat_id)
        elif cmd in ["/info"]:
            await handle_info_cmd(bot, chat_id)
        elif cmd in ["/post"]:
            await handle_post_cmd(bot, chat_id, args, folder_name="blog")
        elif cmd in ["/techpost"]:
            await handle_post_cmd(bot, chat_id, args, folder_name="tech")
        elif cmd in ["/draft"]:
            await handle_draft_cmd(bot, chat_id, args)
        elif cmd in ["/unpublish"]:
            await handle_unpublish_cmd(bot, chat_id, args)
        elif cmd in ["/delete"]:
            await handle_delete_cmd(bot, chat_id, args)
        elif cmd in ["/write"]:
            await handle_write_cmd(bot, chat_id, args)
        return

    # Text message
    if text:
        await handle_text_message(bot, chat_id, text, reply_to)


# --- 4. Main Lambda Handler ---

async def _async_lambda_handler(event, context):
    logger.info(f"Received event: {json.dumps(event, default=str)}")

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN is missing.")
        return {"statusCode": 500, "body": "TELEGRAM_BOT_TOKEN is missing."}

    bot = Bot(token=token)

    # 1. EventBridge Scheduled Event Check (매일 아침 9시 뉴스)
    if event.get("source") == "aws.events" or event.get("detail-type") == "Scheduled Event":
        logger.info("Triggered by EventBridge Scheduled Event.")
        result = await handle_scheduled_news(bot)
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(result)
        }

    # 2. Telegram Webhook (Function URL / API Gateway HTTP POST)
    try:
        body = event.get("body", "{}")
        if event.get("isBase64Encoded", False):
            body = base64.b64decode(body).decode("utf-8")

        if isinstance(body, str):
            update_data = json.loads(body) if body.strip() else {}
        else:
            update_data = body

        if update_data:
            await dispatch_telegram_update(bot, update_data)

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"ok": True})
        }
    except Exception as e:
        logger.error(f"Error handling webhook update: {e}", exc_info=True)
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"ok": False, "error": str(e)})
        }


def lambda_handler(event, context):
    return asyncio.run(_async_lambda_handler(event, context))

