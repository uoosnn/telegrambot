import os
import json
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted
from datetime import datetime
from zoneinfo import ZoneInfo

class AIProcessor:
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not set in environment variables.")
        genai.configure(api_key=api_key)
        
        # 일반 대화용 모델: 자연스러운 대화를 위해 temperature 높임
        chat_config = genai.types.GenerationConfig(
            temperature=0.7,
            top_p=0.9,
            top_k=40
        )
        self.chat_model = genai.GenerativeModel(
            'gemini-2.5-pro',
            generation_config=chat_config,
            system_instruction="당신은 친절하고 유능한 어시스턴트입니다. 자연스럽고 재미있게 대화하되, 사실에 기반하여 답변하세요."
        )
        
        # 블로그 포스트 작성용 모델: 할루시네이션(환각)을 최소화하기 위해 temperature 낮춤
        post_config = genai.types.GenerationConfig(
            temperature=0.2,
            top_p=0.7,
            top_k=30
        )
        self.post_model = genai.GenerativeModel(
            'gemini-2.5-pro',
            generation_config=post_config,
            system_instruction="당신은 사실 기반으로만 글을 작성하는 정확한 블로그 에디터입니다. 제공받은 정보 외에 임의의 사실을 절대 지어내지 마세요. 추측이나 불확실한 내용은 포함하지 마세요."
        )
        
        self.chat_session = None
        self.usage_file = os.path.join(os.path.dirname(__file__), "api_usage.json")
        self._init_usage_stats()
        self.start_new_session()

    def _init_usage_stats(self):
        if not os.path.exists(self.usage_file):
            with open(self.usage_file, 'w', encoding='utf-8') as f:
                json.dump({"input_tokens": 0, "output_tokens": 0}, f)

    def _record_usage(self, response):
        try:
            if hasattr(response, 'usage_metadata'):
                in_tokens = response.usage_metadata.prompt_token_count
                out_tokens = response.usage_metadata.candidates_token_count
                
                with open(self.usage_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                data["input_tokens"] += in_tokens
                data["output_tokens"] += out_tokens
                
                with open(self.usage_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f)
        except Exception:
            pass

    def get_usage_stats(self):
        try:
            with open(self.usage_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {"input_tokens": 0, "output_tokens": 0}

    def _generate_content_with_tracking(self, prompt, model=None):
        """지정된 모델로 콘텐츠를 생성하고 사용량을 추적합니다. 기본값은 post_model."""
        if model is None:
            model = self.post_model
        try:
            response = model.generate_content(prompt)
            self._record_usage(response)
            return response
        except ResourceExhausted:
            raise Exception("API 지출 한도를 초과했습니다 (Quota Exceeded).")
        except Exception as e:
            if "429" in str(e) or "quota" in str(e).lower():
                raise Exception("API 지출 한도를 초과했습니다 (Quota Exceeded).")
            raise e

    def start_new_session(self):
        """새로운 대화 세션을 시작합니다 (기존 문맥 초기화)."""
        self.chat_session = self.chat_model.start_chat(history=[])
        return "새로운 대화가 시작되었습니다. 무엇을 도와드릴까요?"

    def send_message(self, message):
        """사용자의 메시지를 AI에게 전송하고 응답을 받습니다."""
        if not self.chat_session:
            self.start_new_session()
        
        response = self.chat_session.send_message(message)
        self._record_usage(response)
        return response.text.strip()

    def _get_history_text(self):
        """현재 대화 세션의 히스토리를 텍스트로 변환합니다."""
        if not self.chat_session or not self.chat_session.history:
            return ""
        lines = []
        for message in self.chat_session.history:
            role = "사용자" if message.role == "user" else "AI"
            text = message.parts[0].text if message.parts else ""
            lines.append(f"[{role}]: {text}")
        return "\n\n".join(lines)

    def generate_title_from_history(self):
        """현재까지의 대화 내용을 분석하여 블로그 포스트에 적합한 제목을 자동 생성합니다."""
        history_text = self._get_history_text()
        if not history_text:
            return "대화 기록"

        prompt = f"""
아래 대화 내용을 분석하여, 이 대화를 블로그 포스트로 작성할 때 적합한 **제목**을 하나만 생성해 주세요.

[규칙]
1. 제목은 10자~30자 이내로 간결하고 핵심을 담아야 합니다.
2. 오직 제목 텍스트만 출력하세요. (따옴표, 설명, 번호 등 일체 금지)
3. 대화의 주요 주제나 핵심 키워드를 반영하세요.

[대화 내용]
{history_text}
"""
        response = self._generate_content_with_tracking(prompt)
        title = response.text.strip()
        # 혹시 따옴표가 붙어 있다면 제거
        title = title.strip('"').strip("'").strip()
        return title if title else "대화 기록"

    def generate_blog_post_from_history(self, title_instruction):
        """현재까지의 대화 내용을 바탕으로 블로그 포스트를 생성합니다."""
        history_text = self._get_history_text()
        if not history_text:
            return "대화 기록이 없습니다. 먼저 대화를 나누어 주세요."

        kst_now = datetime.now(ZoneInfo("Asia/Seoul"))

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
        response = self._generate_content_with_tracking(prompt)
        return response.text.strip()

    def generate_blog_from_news(self, news_data, user_comment):
        """뉴스 원본 데이터와 사용자의 코멘트를 바탕으로 블로그 포스트 생성"""
        kst_now = datetime.now(ZoneInfo("Asia/Seoul"))
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
3. 오직 제공된 [뉴스 원본] 내용과 [나의 코멘트] 내용만을 바탕으로 작성하며, 절대 없는 내용을 지어내지(할루시네이션) 말 것.
4. 오직 마크다운 텍스트만 출력할 것.
5. **글의 가장 마지막 줄에는 반드시 아래와 같이 게시된 시간을 적어줄 것.**
   
---
*게시된 시간: {kst_now.strftime('%Y-%m-%d %H:%M:%S')}*
"""
        response = self._generate_content_with_tracking(prompt)
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
        response = self._generate_content_with_tracking(prompt)
        return response.text.strip()
