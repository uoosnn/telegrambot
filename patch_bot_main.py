import re
import os

filepath = "/source/telegram_bot/bot_main.py"
with open(filepath, 'r') as f:
    content = f.read()

# 1. State Persistence
state_code = """
# 봇 상태 파일 경로
STATE_FILE = "bot_state.json"

# 봇이 보낸 뉴스 메시지 보관소 (Reply 시 사용)
pending_messages = {}
# 게시 확인 대기 중인 콘텐츠 보관소
pending_confirmations = {}

def load_state():
    global pending_messages, pending_confirmations
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                pending_messages = {int(k): v for k, v in data.get("pending_messages", {}).items()}
                pending_confirmations = {int(k): v for k, v in data.get("pending_confirmations", {}).items()}
        except Exception as e:
            logger.error(f"Error loading state: {e}")

def save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            import json
            json.dump({
                "pending_messages": pending_messages,
                "pending_confirmations": pending_confirmations
            }, f)
    except Exception as e:
        logger.error(f"Error saving state: {e}")

load_state()
"""

content = re.sub(
    r'# 봇이 보낸 뉴스 메시지 보관소.*pending_confirmations = {}',
    state_code.strip(),
    content,
    flags=re.DOTALL
)

# 2. send_news_task
news_fetch_code = """        # 1. 게임 뉴스 가져오기 (1개)
        game_news = await asyncio.to_thread(news_scraper.fetch_game_news, limit=1)
        # 2. 트렌딩 뉴스 가져오기 (2개)
        trending_news = await asyncio.to_thread(news_scraper.fetch_trending_news, limit=2, threshold=2)
        # 3. 일본 시사 뉴스 가져오기 (2개)
        japan_news = await asyncio.to_thread(news_scraper.fetch_japan_news, limit=2)
        
        all_news = game_news + trending_news + japan_news"""

content = re.sub(
    r'        # 1. 게임 뉴스 가져오기\s*game_news = news_scraper.fetch_game_news\(limit=2\)\s*# 2. 트렌딩 뉴스 가져오기\s*trending_news = news_scraper.fetch_trending_news\(threshold=3\)\s*all_news = game_news \+ trending_news',
    news_fetch_code,
    content
)

content = content.replace(
    'save_sent_news(sent_urls)',
    'save_sent_news(sent_urls)\n        save_state()'
)

# 3. _perform_post
content = content.replace(
    'title_instruction = ai_processor.generate_title_from_history()',
    'title_instruction = await asyncio.to_thread(ai_processor.generate_title_from_history)'
)

content = content.replace(
    'markdown_content = ai_processor.generate_blog_post_from_history(title_instruction)',
    'markdown_content = await asyncio.to_thread(ai_processor.generate_blog_post_from_history, title_instruction)'
)

content = content.replace(
    'content_en = ai_processor.translate_blog_post(markdown_content, "English")',
    'content_en = await asyncio.to_thread(ai_processor.translate_blog_post, markdown_content, "English")'
)

content = content.replace(
    'content_ja = ai_processor.translate_blog_post(markdown_content, "Japanese")',
    'content_ja = await asyncio.to_thread(ai_processor.translate_blog_post, markdown_content, "Japanese")'
)

content = content.replace(
    'success, result = github_uploader.save_and_push(',
    'success, result = await asyncio.to_thread(github_uploader.save_and_push,'
)

# 4. handle_message
content = content.replace(
    'markdown_content = ai_processor.generate_blog_from_news(news_info, user_text)',
    'markdown_content = await asyncio.to_thread(ai_processor.generate_blog_from_news, news_info, user_text)'
)

content = content.replace(
    "'news_info': news_info\n                }",
    "'news_info': news_info\n                }\n                save_state()"
)

content = content.replace(
    'reply_text = ai_processor.send_message(user_text)',
    'reply_text = await asyncio.to_thread(ai_processor.send_message, user_text)'
)

# 5. handle_photo
content = content.replace(
    'success, result = github_uploader.save_image_and_push(file_bytes, filename)',
    'success, result = await asyncio.to_thread(github_uploader.save_image_and_push, file_bytes, filename)'
)

content = content.replace(
    'ai_processor.send_message(context_msg)',
    'await asyncio.to_thread(ai_processor.send_message, context_msg)'
)

# 6. handle_confirmation_callback
content = content.replace(
    'pending_messages.pop(reply_to_id, None)',
    'pending_messages.pop(reply_to_id, None)\n                save_state()'
)

content = content.replace(
    'pending_confirmations.pop(reply_to_id, None)',
    'pending_confirmations.pop(reply_to_id, None)\n        save_state()'
)

# 7. write_command
content = content.replace(
    'success, result = github_uploader.save_and_push(user_text, title=title)',
    'success, result = await asyncio.to_thread(github_uploader.save_and_push, user_text, title=title)'
)

# 8. sync_command
content = content.replace(
    'content_en = ai_processor.translate_blog_post(content, "English")',
    'content_en = await asyncio.to_thread(ai_processor.translate_blog_post, content, "English")'
)

content = content.replace(
    'content_ja = ai_processor.translate_blog_post(content, "Japanese")',
    'content_ja = await asyncio.to_thread(ai_processor.translate_blog_post, content, "Japanese")'
)

# Wait, asyncio.to_thread syntax: asyncio.to_thread(func, *args, **kwargs). 
# kwargs are supported. We are good.
# Let's write back.
with open(filepath, 'w') as f:
    f.write(content)
print("bot_main.py patched successfully.")
