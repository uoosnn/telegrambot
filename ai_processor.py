import os
import google.generativeai as genai
from datetime import datetime

class AIProcessor:
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not set in environment variables.")
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel('gemini-2.5-pro')
        self.chat_session = None
        self.start_new_session()

    def start_new_session(self):
        """새로운 대화 세션을 시작합니다 (기존 문맥 초기화)."""
        self.chat_session = self.model.start_chat(history=[])
        return "새로운 대화가 시작되었습니다. 무엇을 도와드릴까요?"

    def send_message(self, message):
        """사용자의 메시지를 AI에게 전송하고 응답을 받습니다."""
        if not self.chat_session:
            self.start_new_session()
        
        try:
            response = self.chat_session.send_message(message)
            return response.text.strip()
        except Exception as e:
            return f"❌ 오류가 발생했습니다: {str(e)}"

    def generate_blog_post_from_history(self, title_instruction):
        """현재까지의 대화 내용을 바탕으로 블로그 포스트를 생성합니다."""
        if not self.chat_session or not self.chat_session.history:
            return "대화 기록이 없습니다. 먼저 대화를 나누어 주세요."

        # 대화 기록 텍스트화
        history_text = ""
        for message in self.chat_session.history:
            role = "사용자" if message.role == "user" else "AI"
            text = message.parts[0].text if message.parts else ""
            history_text += f"[{role}]: {text}\n\n"

        kst_now = datetime.utcnow() + timedelta(hours=9)

        prompt = f"""
당신은 나와 대화했던 내용을 바탕으로 훌륭한 기술/일상 블로그 포스트를 작성하는 에디터입니다.
아래의 [대화 기록]을 분석하여, 사용자가 '/post' 명령어를 통해 요청한 [{title_instruction}]에 맞는 마크다운(Markdown) 포스트를 작성해 주세요.

[요구사항]
1. VitePress 기반 블로그이므로 마크다운 최상단에 반드시 Frontmatter(yaml 형식)를 포함할 것.
   - title: {title_instruction if title_instruction else '대화 요약'} (적절히 다듬어주세요)
   - date: {kst_now.strftime('%Y-%m-%d')}
   - tags: [AI, 대화요약]
2. 대화 내용을 보기 좋게 정리하고, 필요하다면 서론-본론-결론 구조를 갖출 것.
3. 오직 마크다운 텍스트 결과물만 출력할 것.
4. **글의 가장 마지막 줄에는 반드시 아래와 같이 게시된 시간을 적어줄 것.**

---
*게시된 시간: {kst_now.strftime('%Y-%m-%d %H:%M:%S')}*

[대화 기록]
{history_text}
"""
        response = self.model.generate_content(prompt)
        return response.text.strip()

    def generate_blog_from_news(self, news_data, user_comment):
        """뉴스 원본 데이터와 사용자의 코멘트를 바탕으로 블로그 포스트 생성"""
        from datetime import timedelta
        kst_now = datetime.utcnow() + timedelta(hours=9)
        prompt = f"""
당신은 기술 및 게임 트렌드 블로그 전문 에디터입니다.
아래의 [뉴스 원본]과 [나의 코멘트]를 분석하여 훌륭한 마크다운(Markdown) 포스트를 작성해 주세요.

[뉴스 원본]
- 제목: {news_data.get('title')}
- 링크: {news_data.get('url')}

[나의 코멘트]
{user_comment}

[요구사항]
1. VitePress 기반 블로그이므로 마크다운 최상단에 반드시 Frontmatter(yaml 형식)를 포함할 것.
   - title: 매력적인 제목 (뉴스 제목과 코멘트 조합)
   - date: {kst_now.strftime('%Y-%m-%d')}
   - tags: [뉴스, {news_data.get('category', 'trend')}]
2. '뉴스 요약' -> '나의 생각(코멘트 바탕)' -> '결론 및 원문 링크' 구조로 작성할 것.
3. 오직 마크다운 텍스트만 출력할 것.
4. **글의 가장 마지막 줄에는 반드시 아래와 같이 게시된 시간을 적어줄 것.**
   
---
*게시된 시간: {kst_now.strftime('%Y-%m-%d %H:%M:%S')}*
"""
        response = self.model.generate_content(prompt)
        return response.text.strip()
