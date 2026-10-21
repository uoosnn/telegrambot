import os
import json
import base64
import logging
import asyncio
import uuid
import hashlib
import re
from datetime import datetime
from zoneinfo import ZoneInfo
import boto3
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
AWS_REGION = os.getenv("AWS_REGION", "ap-northeast-2")

dynamo_mgr = DynamoManager()
ai_processor = AIProcessor(dynamo_mgr=dynamo_mgr)
github_uploader = GithubUploader()
news_scraper = NewsScraper()

POSTS_PER_PAGE = 8


# --- Helper: Async Self-Invocation (0.2s Webhook Response) ---

def is_heavy_update(update_data: dict) -> bool:
    """
    AI 블로그 생성/번역 등 10초 이상 소요되어 텔레그램 웹훅 타임아웃 재시도를 유발할 수 있는
    무거운 작업인지 판별합니다.
    """
    if "callback_query" in update_data:
        cb_data = update_data["callback_query"].get("data", "")
        if cb_data.startswith(("confirm_post_", "del_exec_", "unpub_exec_")):
            return True
        return False

    message = update_data.get("message") or update_data.get("edited_message")
    if not message:
        return False

    text = (message.get("text") or "").strip()
    if text.startswith("/"):
        cmd = text.split(maxsplit=1)[0].lower()
        if cmd in ["/post", "/techpost", "/draft", "/write"]:
            return True
        return False

    # 뉴스 알림에 대한 답장이나 일반 AI 채팅도 시간이 걸릴 수 있으므로 비동기 위임 대상
    if text or message.get("photo"):
        return True

    return False


def invoke_async_worker(update_data: dict, context) -> bool:
    """
    AWS Lambda 자신을 비동기(InvocationType='Event')로 호출하여
    텔레그램 웹훅에 0.2초 내로 즉시 200 OK를 반환합니다.
    (만약 IAM Role에 lambda:InvokeFunction 권한이 없으면 False를 반환하여 현재 프로세스에서 안전하게 폴백 실행)
    """
    if not context:
        return False
    fn_name = getattr(context, "invoked_function_arn", None) or getattr(context, "function_name", None)
    if not fn_name:
        return False

    try:
        lambda_client = boto3.client("lambda", region_name=AWS_REGION)
        payload = {
            "is_async_worker": True,
            "update_data": update_data
        }
        response = lambda_client.invoke(
            FunctionName=fn_name,
            InvocationType="Event",
            Payload=json.dumps(payload).encode("utf-8")
        )
        status_code = response.get("StatusCode", 0)
        if status_code in (200, 202):
            logger.info(f"[AsyncWorker] Dispatched update_id={update_data.get('update_id')} asynchronously (HTTP {status_code}).")
            return True
    except Exception as e:
        logger.warning(f"[AsyncWorker] Self-invocation unavailable ({e}). Falling back to inline execution with DynamoDB lock.")
    return False


# --- Helper: Token ID & Post Management UI ---

def make_post_token(folder: str, filename: str) -> str:
    raw = f"{folder}/{filename}".encode("utf-8")
    token_id = hashlib.md5(raw).hexdigest()[:10]
    dynamo_mgr.save_delete_target(token_id, folder, filename)
    return token_id


def resolve_post_token(token_id: str):
    target = dynamo_mgr.get_delete_target(token_id)
    if target and target.get("folder") and target.get("filename"):
        return target["folder"], target["filename"]

    # Fallback: DynamoDB TTL 만료 시 저장소 파일 목록에서 해시 매칭으로 복원
    all_posts = github_uploader.list_all_posts()
    for folder, filename in all_posts:
        raw = f"{folder}/{filename}".encode("utf-8")
        if hashlib.md5(raw).hexdigest()[:10] == token_id:
            dynamo_mgr.save_delete_target(token_id, folder, filename)
            return folder, filename
    return None, None


def build_post_Complete_markup(folder: str, filename: str) -> InlineKeyboardMarkup:
    token_id = make_post_token(folder, filename)
    keyboard = [
        [
            InlineKeyboardButton("🗑️ 방금 올린 글 삭제", callback_data=f"del_sel_{token_id}"),
            InlineKeyboardButton("🙈 비공개 전환", callback_data=f"unpub_exec_{token_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def build_post_confirm_view(folder: str, filename: str):
    token_id = make_post_token(folder, filename)
    badge = "🛠️ Tech" if folder == "tech" else "📝 Blog"
    display_title = filename[:-3] if filename.endswith(".md") else filename
    text = (
        f"📌 **게시글 관리 선택**\n\n"
        f"• **분류:** {badge} (`{folder}`)\n"
        f"• **제목:** {display_title}\n"
        f"• **파일:** `{filename}`\n\n"
        f"⚠️ **영구 삭제** 시 3개 국어(KO/EN/JA) 파일, 기술 색인(`tech/index.md`), 본문 내 첨부 이미지가 **단 1번의 커밋**으로 모두 삭제됩니다."
    )
    keyboard = [
        [
            InlineKeyboardButton("🗑️ 영구 삭제 (KO/EN/JA)", callback_data=f"del_exec_{token_id}"),
            InlineKeyboardButton("🙈 비공개 전환 (draft)", callback_data=f"unpub_exec_{token_id}")
        ],
        [
            InlineKeyboardButton("📋 전체 목록 보기", callback_data="del_page_0"),
            InlineKeyboardButton("❌ 취소", callback_data="del_close")
        ]
    ]
    return text, InlineKeyboardMarkup(keyboard)


def build_post_list_view(posts: list, page: int = 0, header_text: str = None):
    total = len(posts)
    total_pages = max(1, (total + POSTS_PER_PAGE - 1) // POSTS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start_idx = page * POSTS_PER_PAGE
    page_items = posts[start_idx:start_idx + POSTS_PER_PAGE]

    keyboard = []
    for folder, filename in page_items:
        token_id = make_post_token(folder, filename)
        icon = "🛠️" if folder == "tech" else "📝"
        clean_name = filename[:-3] if filename.endswith(".md") else filename
        if len(clean_name) > 28:
            clean_name = clean_name[:27] + "…"
        keyboard.append([
            InlineKeyboardButton(f"{icon} [{folder}] {clean_name}", callback_data=f"del_sel_{token_id}")
        ])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ 이전", callback_data=f"del_page_{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("다음 ▶️", callback_data=f"del_page_{page + 1}"))
    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton("❌ 닫기", callback_data="del_close")])

    if not header_text:
        header_text = (
            f"🗑️ **삭제 또는 비공개할 게시글을 선택하세요**\n"
            f"*(총 {total}개 게시글 | 페이지 {page + 1}/{total_pages})*\n\n"
            f"💡 최근에 올린 글이 상단에 표시됩니다. 버튼을 누르면 확인 창으로 이동합니다."
        )
    return header_text, InlineKeyboardMarkup(keyboard)


# --- Helper: Split Message Sending & Safe Editing ---
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


async def safe_edit_message(bot: Bot, chat_id: int, message_id: int, text: str, reply_markup=None):
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=reply_markup)
    except Exception as e:
        logger.warning(f"Failed to edit message {message_id}: {e}")


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
        "/delete [키워드(생략가능)] - 버튼으로 간편하게 게시글 영구 삭제 및 비공개 전환\n"
        "/unpublish [키워드(생략가능)] - 기존 발행 글을 비공개(초안)로 전환\n"
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
        "/delete [키워드(생략가능)] - 최근 글 목록 또는 검색 결과에서 클릭하여 삭제/비공개\n"
        "/unpublish [키워드(생략가능)] - 기존 발행된 글을 비공개(초안)로 전환\n"
        "/write - 마크다운 직접 입력 포스팅\n"
        "/usage - API 사용량 및 예상 요금 확인\n"
        "/info - 이 명령어 목록 표시\n\n"
        "💡 *키워드 없이 `/delete`만 입력하면 전체 게시글을 버튼으로 보며 쉽게 삭제할 수 있습니다.*"
    )
    await bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')

async def handle_draft_cmd(bot: Bot, chat_id: int, title_arg: str):
    if not dynamo_mgr.acquire_post_lock(chat_id, ttl_sec=120):
        await bot.send_message(chat_id=chat_id, text="⚠️ 이미 게시글 생성 작업이 진행 중입니다. 완료될 때까지 잠시만 기다려 주세요.")
        return

    try:
        title_instruction = title_arg.strip() if title_arg else None
        status_msg = await bot.send_message(chat_id=chat_id, text="📝 [1/2] 비공개 초안(draft: true) 제목 및 본문을 생성 중입니다...")

        if not title_instruction:
            try:
                title_instruction = await asyncio.to_thread(ai_processor.generate_title_from_history, chat_id)
            except Exception:
                title_instruction = f"초안 노트 ({datetime.now().strftime('%Y-%m-%d')})"

        markdown_content = await asyncio.to_thread(ai_processor.generate_blog_post_from_history, title_instruction, chat_id)
        await safe_edit_message(bot, chat_id, status_msg.message_id, f"🚀 [2/2] GitHub에 비공개 초안 저장 중... (제목: {title_instruction})")

        success, result = await asyncio.to_thread(
            github_uploader.save_and_push_draft,
            markdown_content,
            title=title_instruction,
            folder_name="blog"
        )
        if success:
            markup = None
            if github_uploader.last_uploaded:
                folder, filename = github_uploader.last_uploaded
                dynamo_mgr.record_recent_post(folder, filename)
                markup = build_post_Complete_markup(folder, filename)
            await safe_edit_message(
                bot,
                chat_id,
                status_msg.message_id,
                f"✅ 비공개 초안 저장 성공!\n\n저장 경로: {result}\n*(※ draft: true가 설정되어 사이트 목록 및 Google 검색엔진 색인에서 자동 제외됩니다)*",
                reply_markup=markup
            )
        else:
            await safe_edit_message(bot, chat_id, status_msg.message_id, f"❌ 깃허브 업로드 실패:\n{result}")
    except Exception as e:
        logger.error(f"Error in handle_draft_cmd: {e}", exc_info=True)
        await bot.send_message(chat_id=chat_id, text=f"❌ 오류 발생: {str(e)}")
    finally:
        dynamo_mgr.release_post_lock(chat_id)

async def handle_delete_or_unpublish_cmd(bot: Bot, chat_id: int, keyword: str):
    """
    /delete 및 /unpublish 통합 대화형 핸들러:
    - 키워드 미입력 시: 최근 게시글을 포함한 전체 게시글 버튼 목록(페이지네이션 지원) 표시
    - 키워드 입력 시:
      - 1건 일치: 즉시 [영구 삭제] / [비공개 전환] 2단계 확인 카드 표시
      - 다수 일치: 일치하는 게시글들을 인라인 버튼 목록으로 표시하여 재입력 없이 클릭 선택
    """
    kw = keyword.strip()
    try:
        if not kw:
            recent_hints = dynamo_mgr.get_recent_posts()
            all_posts = await asyncio.to_thread(github_uploader.list_all_posts, recent_hints)
            if not all_posts:
                await bot.send_message(chat_id=chat_id, text="⚠️ 저장소에서 게시글을 찾을 수 없습니다.")
                return
            text, markup = build_post_list_view(all_posts, page=0)
            await bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown', reply_markup=markup)
            return

        matches = await asyncio.to_thread(github_uploader.find_post_files, kw)
        if not matches:
            keyboard = [[InlineKeyboardButton("📋 전체 게시글 목록 보기", callback_data="del_page_0")]]
            await bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ '{kw}'에 해당하는 게시글을 찾을 수 없습니다.\n전체 목록에서 직접 선택해 보세요.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        if len(matches) == 1:
            folder, filename, _ = matches[0]
            text, markup = build_post_confirm_view(folder, filename)
            await bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown', reply_markup=markup)
            return

        # 복수 매칭 시 버튼 목록으로 제시
        matched_pairs = [(f, n) for f, n, _ in matches]
        header = (
            f"🔍 **'{kw}' 검색 결과 ({len(matched_pairs)}건)**\n\n"
            f"삭제하거나 비공개로 전환할 게시글을 아래 버튼에서 선택해 주세요:"
        )
        text, markup = build_post_list_view(matched_pairs, page=0, header_text=header)
        await bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown', reply_markup=markup)

    except Exception as e:
        logger.error(f"Error in handle_delete_or_unpublish_cmd: {e}", exc_info=True)
        await bot.send_message(chat_id=chat_id, text=f"❌ 오류 발생: {str(e)}")

async def handle_post_cmd(bot: Bot, chat_id: int, title_arg: str, folder_name="blog"):
    # 분산 락(Post Lock)으로 중복 실행 원천 차단
    if not dynamo_mgr.acquire_post_lock(chat_id, ttl_sec=120):
        await bot.send_message(
            chat_id=chat_id,
            text="⚠️ 이미 게시글 생성 및 번역 작업이 진행 중입니다. 현재 작업이 완료될 때까지 잠시만 기다려 주세요."
        )
        return

    target_name = "기술 블로그(tech)" if folder_name == "tech" else "블로그(blog)"
    title_instruction = title_arg.strip() if title_arg else None

    try:
        # 여러 개의 새 메시지를 보내지 않고, 단일 상태 메시지를 수정(edit_message_text)하여 진행 상황 표시
        status_msg = await bot.send_message(
            chat_id=chat_id,
            text=f"⏳ [1/3] 대화 내용을 바탕으로 {target_name} 본문을 생성 중입니다..."
        )
        msg_id = status_msg.message_id

        if not title_instruction:
            try:
                title_instruction = await asyncio.to_thread(ai_processor.generate_title_from_history, chat_id)
            except Exception as e:
                logger.error(f"Error generating title: {e}")
                title_instruction = f"대화 기록 ({datetime.now().strftime('%Y-%m-%d')})"

        await safe_edit_message(
            bot, chat_id, msg_id,
            f"⏳ [1/3] {target_name} 마크다운 초안 작성 중...\n• 주제: {title_instruction}"
        )

        markdown_content = await asyncio.to_thread(ai_processor.generate_blog_post_from_history, title_instruction, chat_id)
        if folder_name == "tech":
            markdown_content = markdown_content.replace("tags: [AI, 대화요약]", "tags: [Tech, AI, 기술분석]")

        await safe_edit_message(
            bot, chat_id, msg_id,
            f"🌐 [2/3] 영문(EN) 및 일문(JA) 자동 번역을 동시 수행 중...\n• 주제: {title_instruction}"
        )
        content_en, content_ja = await asyncio.gather(
            asyncio.to_thread(ai_processor.translate_blog_post, markdown_content, "English"),
            asyncio.to_thread(ai_processor.translate_blog_post, markdown_content, "Japanese")
        )

        await safe_edit_message(
            bot, chat_id, msg_id,
            f"🚀 [3/3] GitHub 저장소에 3개 국어 파일 및 색인을 단일 커밋으로 업로드 중..."
        )
        success, result = await asyncio.to_thread(
            github_uploader.save_and_push,
            markdown_content,
            title=title_instruction,
            folder_name=folder_name,
            translations={'en': content_en, 'ja': content_ja}
        )

        if success:
            markup = None
            if github_uploader.last_uploaded:
                folder, filename = github_uploader.last_uploaded
                dynamo_mgr.record_recent_post(folder, filename)
                markup = build_post_Complete_markup(folder, filename)
            await safe_edit_message(
                bot,
                chat_id,
                msg_id,
                f"✅ {target_name} 포스팅 성공! (단일 커밋 완료)\n\n저장 경로: {result}",
                reply_markup=markup
            )
        else:
            await safe_edit_message(bot, chat_id, msg_id, f"❌ 깃허브 업로드 실패:\n{result}")
    except Exception as e:
        logger.error(f"Error during post: {e}", exc_info=True)
        await bot.send_message(chat_id=chat_id, text=f"❌ 오류 발생: {str(e)}")
    finally:
        dynamo_mgr.release_post_lock(chat_id)

async def handle_write_cmd(bot: Bot, chat_id: int, user_text: str):
    if not user_text:
        await bot.send_message(chat_id=chat_id, text="❌ 작성할 내용(프론트매터 포함 마크다운)을 함께 적어주세요!\n예: `/write ---\ntitle: 제목\n...`")
        return

    if not dynamo_mgr.acquire_post_lock(chat_id, ttl_sec=60):
        await bot.send_message(chat_id=chat_id, text="⚠️ 이미 업로드 작업이 진행 중입니다. 잠시만 기다려 주세요.")
        return

    try:
        status_msg = await bot.send_message(chat_id=chat_id, text="📝 수동 포스트 업로드를 진행 중입니다...")
        title_match = re.search(r'title:\s*"?([^"\n]+)"?', user_text)
        title = title_match.group(1).strip() if title_match else f"직접 작성한 글 ({datetime.now().strftime('%Y-%m-%d %H%M%S')})"

        success, result = await asyncio.to_thread(github_uploader.save_and_push, user_text, title=title)
        if success:
            markup = None
            if github_uploader.last_uploaded:
                folder, filename = github_uploader.last_uploaded
                dynamo_mgr.record_recent_post(folder, filename)
                markup = build_post_Complete_markup(folder, filename)
            await safe_edit_message(
                bot,
                chat_id,
                status_msg.message_id,
                f"✅ 블로그 수동 포스팅 성공!\n\n저장 경로: {result}",
                reply_markup=markup
            )
        else:
            await safe_edit_message(bot, chat_id, status_msg.message_id, f"❌ 깃허브 업로드 실패:\n{result}")
    except Exception as e:
        logger.error(f"Error during write: {e}", exc_info=True)
        await bot.send_message(chat_id=chat_id, text=f"❌ 오류 발생: {str(e)}")
    finally:
        dynamo_mgr.release_post_lock(chat_id)

async def handle_text_message(bot: Bot, chat_id: int, user_text: str, reply_to_msg: dict = None):
    # 1. 뉴스 메시지에 답장(Reply)한 경우
    if reply_to_msg:
        reply_to_id = reply_to_msg.get("message_id")
        news_info = dynamo_mgr.get_pending_message(reply_to_id)

        if news_info:
            status_msg = await bot.send_message(chat_id=chat_id, text="⏳ 코멘트를 확인했습니다. 뉴스를 분석하여 블로그 포스트 초안을 작성 중입니다...")
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
                await safe_edit_message(bot, chat_id, status_msg.message_id, preview_msg, reply_markup=reply_markup)
            except Exception as e:
                logger.error(f"Error generating news post: {e}", exc_info=True)
                await safe_edit_message(bot, chat_id, status_msg.message_id, f"❌ 오류 발생: {str(e)}")
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
    status_msg = await bot.send_message(chat_id=chat_id, text="⏳ 이미지를 깃허브에 업로드 중입니다...")
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
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg.message_id,
                text=f"✅ 이미지 업로드 완료!\n\nAI가 이 이미지를 인지했습니다. 바로 사용할 수 있는 마크다운 링크입니다:\n`{result}`",
                parse_mode='Markdown'
            )
        else:
            await safe_edit_message(bot, chat_id, status_msg.message_id, f"❌ 깃허브 업로드 실패:\n{result}")
    except Exception as e:
        logger.error(f"Error uploading photo: {e}", exc_info=True)
        await safe_edit_message(bot, chat_id, status_msg.message_id, f"❌ 오류 발생: {str(e)}")

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

    # --- A. 뉴스 기반 포스팅 확인/취소 ---
    if data.startswith("confirm_post_"):
        reply_to_id = int(data.replace("confirm_post_", ""))
        if not dynamo_mgr.acquire_post_lock(chat_id, ttl_sec=120):
            return

        try:
            confirmation = dynamo_mgr.get_pending_confirmation(reply_to_id)
            if not confirmation:
                await safe_edit_message(bot, chat_id, msg_id, "⚠️ 해당 게시글 데이터를 찾을 수 없습니다. (만료되었거나 이미 처리됨)")
                return

            # 중복 클릭/재시도 방지를 위해 확인 데이터 선제 삭제
            dynamo_mgr.delete_pending_confirmation(reply_to_id)
            markdown_content = confirmation['markdown_content']
            await safe_edit_message(bot, chat_id, msg_id, "🌐 [1/2] 영문 및 일문으로 자동 번역을 동시에 수행 중입니다...")

            content_en, content_ja = await asyncio.gather(
                asyncio.to_thread(ai_processor.translate_blog_post, markdown_content, "English"),
                asyncio.to_thread(ai_processor.translate_blog_post, markdown_content, "Japanese")
            )

            await safe_edit_message(bot, chat_id, msg_id, "🚀 [2/2] GitHub 저장소에 단일 커밋으로 업로드 중입니다...")
            success, result = await asyncio.to_thread(
                github_uploader.save_and_push,
                markdown_content,
                translations={'en': content_en, 'ja': content_ja}
            )

            if success:
                markup = None
                if github_uploader.last_uploaded:
                    folder, filename = github_uploader.last_uploaded
                    dynamo_mgr.record_recent_post(folder, filename)
                    markup = build_post_Complete_markup(folder, filename)
                await safe_edit_message(
                    bot,
                    chat_id,
                    msg_id,
                    f"✅ 뉴스 기반 블로그 포스팅 성공! (단일 커밋 완료)\n\n저장 경로: {result}",
                    reply_markup=markup
                )
                dynamo_mgr.delete_pending_message(reply_to_id)
            else:
                await safe_edit_message(bot, chat_id, msg_id, f"❌ 깃허브 업로드 실패:\n{result}")
        except Exception as e:
            logger.error(f"Error posting confirmed news: {e}", exc_info=True)
            await safe_edit_message(bot, chat_id, msg_id, f"❌ 오류 발생: {str(e)}")
        finally:
            dynamo_mgr.release_post_lock(chat_id)

    elif data.startswith("cancel_post_"):
        reply_to_id = int(data.replace("cancel_post_", ""))
        dynamo_mgr.delete_pending_confirmation(reply_to_id)
        await safe_edit_message(bot, chat_id, msg_id, "❌ 게시가 취소되었습니다.")

    # --- B. 게시글 간편 삭제 및 비공개 관리 콜백 ---
    elif data == "del_close":
        await safe_edit_message(bot, chat_id, msg_id, "✅ 게시글 관리 창을 닫았습니다.")

    elif data.startswith("del_page_"):
        page = int(data.replace("del_page_", ""))
        recent_hints = dynamo_mgr.get_recent_posts()
        all_posts = await asyncio.to_thread(github_uploader.list_all_posts, recent_hints)
        if not all_posts:
            await safe_edit_message(bot, chat_id, msg_id, "⚠️ 저장소에서 게시글을 찾을 수 없습니다.")
            return
        text, markup = build_post_list_view(all_posts, page=page)
        await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, parse_mode='Markdown', reply_markup=markup)

    elif data.startswith("del_sel_"):
        token_id = data.replace("del_sel_", "")
        folder, filename = await asyncio.to_thread(resolve_post_token, token_id)
        if not folder or not filename:
            await safe_edit_message(bot, chat_id, msg_id, "⚠️ 선택한 게시글 정보를 찾을 수 없거나 이미 삭제되었습니다.")
            return
        text, markup = build_post_confirm_view(folder, filename)
        await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, parse_mode='Markdown', reply_markup=markup)

    elif data.startswith("del_exec_"):
        token_id = data.replace("del_exec_", "")
        folder, filename = await asyncio.to_thread(resolve_post_token, token_id)
        if not folder or not filename:
            await safe_edit_message(bot, chat_id, msg_id, "⚠️ 삭제할 게시글 정보를 찾을 수 없거나 이미 삭제되었습니다.")
            return

        await safe_edit_message(
            bot,
            chat_id,
            msg_id,
            f"⏳ [{folder}] {filename} 게시글(KO/EN/JA), 색인 및 첨부 이미지를 단일 커밋으로 영구 삭제 중입니다..."
        )
        try:
            success, result = await asyncio.to_thread(github_uploader.delete_post_by_target, folder, filename)
            if success:
                dynamo_mgr.remove_recent_post(folder, filename)
                dynamo_mgr.delete_delete_target(token_id)
                await safe_edit_message(bot, chat_id, msg_id, f"✅ {result}")
            else:
                await safe_edit_message(bot, chat_id, msg_id, f"⚠️ {result}")
        except Exception as e:
            logger.error(f"Error in del_exec callback: {e}", exc_info=True)
            await safe_edit_message(bot, chat_id, msg_id, f"❌ 삭제 중 오류 발생: {str(e)}")

    elif data.startswith("unpub_exec_"):
        token_id = data.replace("unpub_exec_", "")
        folder, filename = await asyncio.to_thread(resolve_post_token, token_id)
        if not folder or not filename:
            await safe_edit_message(bot, chat_id, msg_id, "⚠️ 비공개 전환할 게시글 정보를 찾을 수 없습니다.")
            return

        await safe_edit_message(
            bot,
            chat_id,
            msg_id,
            f"⏳ [{folder}] {filename} 게시글을 비공개(draft: true)로 전환 중입니다..."
        )
        try:
            success, result = await asyncio.to_thread(github_uploader.unpublish_post_by_target, folder, filename)
            if success:
                await safe_edit_message(
                    bot,
                    chat_id,
                    msg_id,
                    f"✅ {result}\n*(※ 사이트 목록 및 검색엔진 색인에서 제외됩니다)*"
                )
            else:
                await safe_edit_message(bot, chat_id, msg_id, f"⚠️ {result}")
        except Exception as e:
            logger.error(f"Error in unpub_exec callback: {e}", exc_info=True)
            await safe_edit_message(bot, chat_id, msg_id, f"❌ 비공개 전환 중 오류 발생: {str(e)}")


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
        elif cmd in ["/delete", "/unpublish", "/manage", "/posts"]:
            await handle_delete_or_unpublish_cmd(bot, chat_id, args)
        elif cmd in ["/write"]:
            await handle_write_cmd(bot, chat_id, args)
        return

    # Text message
    if text:
        await handle_text_message(bot, chat_id, text, reply_to)


# --- 4. Main Lambda Handler ---

async def _async_lambda_handler(event, context):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN is missing.")
        return {"statusCode": 500, "body": "TELEGRAM_BOT_TOKEN is missing."}

    bot = Bot(token=token)

    # 0. 비동기 워커 호출(Self-Invocation) 처리
    if event.get("is_async_worker"):
        update_data = event.get("update_data", {})
        logger.info(f"[AsyncWorker] Executing background update_id={update_data.get('update_id')}")
        await dispatch_telegram_update(bot, update_data)
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"ok": True, "worker": True})
        }

    logger.info(f"Received event: {json.dumps(event, default=str)}")

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
            # [방어 1계층] DynamoDB update_id 원자적 선점 (멱등성 보장: 텔레그램 웹훅 타임아웃 재시도 즉시 차단)
            update_id = update_data.get("update_id")
            if update_id is not None and not dynamo_mgr.claim_update_id(update_id):
                return {
                    "statusCode": 200,
                    "headers": {"Content-Type": "application/json"},
                    "body": json.dumps({"ok": True, "duplicate_ignored": True})
                }

            # [방어 2계층] 무거운 작업(/post, /techpost 등)은 비동기 워커(Self-Invocation)로 위임하여 0.2초 내 200 OK 반환
            if is_heavy_update(update_data) and invoke_async_worker(update_data, context):
                return {
                    "statusCode": 200,
                    "headers": {"Content-Type": "application/json"},
                    "body": json.dumps({"ok": True, "async_dispatched": True})
                }

            # 단문 명령어(/start, /info, /delete 목록 등) 또는 Self-Invocation 권한 미설정 시 인라인 실행
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
