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
   - title: "{title_instruction if title_instruction else '대화 요약'}" (반드시 양끝에 큰따옴표를 붙일 것)
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
   - title: "매력적인 제목" (반드시 양끝에 큰따옴표를 붙일 것)
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

    def translate_blog_post(self, markdown_text, target_language):
        """기존 마크다운 포스트를 지정된 언어로 번역합니다. (프론트매터 및 마크다운 구조 유지)"""
        prompt = f"""
당신은 완벽한 IT/블로그 전문 번역가입니다.
아래 제공된 [원본 마크다운] 문서를 **{target_language}**로 번역해 주세요.

[요구사항]
1. 최상단의 Frontmatter(yaml 형식) 블록은 그대로 유지하되, `title`의 값은 해당 언어로 번역하고 **반드시 큰따옴표("")**로 묶어주세요. (`date`와 `tags`는 원본과 동일하게 유지)
2. 마크다운의 구조(코드 블록, 인용구, 링크, 볼드체 등)는 절대 훼손하지 마세요.
3. 글의 내용과 맥락을 가장 자연스럽고 매끄러운 {target_language}로 번역하세요.
4. 오직 번역된 마크다운 결과물만 출력하세요. (설명이나 인사말 금지)

[원본 마크다운]
{markdown_text}
"""
        response = self.model.generate_content(prompt)
        # 번역본에서도 백틱 래퍼가 있다면 제거
        translated = response.text.strip()
        if translated.startswith("```markdown"):
            translated = translated[11:]
        if translated.startswith("```"):
            translated = translated[3:]
        if translated.endswith("```"):
            translated = translated[:-3]
        return translated.strip()
