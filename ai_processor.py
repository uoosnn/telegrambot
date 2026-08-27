import os
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted

from dynamo_manager import DynamoManager

logger = logging.getLogger(__name__)

class AIProcessor:
    def __init__(self, dynamo_mgr=None):
        api_key = os.getenv("GEMINI_API_KEY")
        self.dynamo_mgr = dynamo_mgr or DynamoManager()
        self.chat_model = None
        self.post_model = None
        self.trans_model = None

        if not api_key:
            logger.warning("GEMINI_API_KEY is not set in environment variables.")
            return

        try:
            genai.configure(api_key=api_key)
            # 일반 대화용 모델
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
            
            # 블로그 포스트 작성용 모델
            post_config = genai.types.GenerationConfig(
                temperature=0.6,
                top_p=0.8,
                top_k=40
            )
            self.post_model = genai.GenerativeModel(
                'gemini-2.5-pro',
                generation_config=post_config,
                system_instruction="당신은 사실 기반으로만 글을 작성하는 정확한 블로그 에디터입니다. 제공받은 정보 외에 임의의 사실을 절대 지어내지 마세요. 추측이나 불확실한 내용은 포함하지 마세요."
            )
            
            # 단순 번역용 모델
            trans_config = genai.types.GenerationConfig(
                temperature=0.3,
                top_p=0.8,
                top_k=40
            )
            self.trans_model = genai.GenerativeModel(
                'gemini-2.5-flash',
                generation_config=trans_config,
                system_instruction="당신은 원본의 형식과 마크다운 문법을 완벽히 보존하는 전문 번역가입니다."
            )
        except Exception as e:
            logger.error(f"Error configuring Gemini: {e}")


    def _record_usage(self, response):
        try:
            if hasattr(response, 'usage_metadata'):
                in_tokens = response.usage_metadata.prompt_token_count
                out_tokens = response.usage_metadata.candidates_token_count
                if self.dynamo_mgr:
                    self.dynamo_mgr.record_usage(in_tokens, out_tokens)
        except Exception as e:
            logger.warning(f"Error recording token usage: {e}")

    def get_usage_stats(self):
        if self.dynamo_mgr:
            return self.dynamo_mgr.get_usage_stats()
        return {"input_tokens": 0, "output_tokens": 0}

    def _generate_content_with_tracking(self, prompt, model=None):
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

    def start_new_session(self, chat_id: int):
        """특정 사용자의 대화 세션을 초기화합니다."""
        if self.dynamo_mgr:
            self.dynamo_mgr.clear_chat_history(chat_id)
        return "새로운 대화가 시작되었습니다. 무엇을 도와드릴까요?"

    def _sanitize_gemini_history(self, history: list):
        sanitized = []
        for item in history:
            text = (item.get("text") or "").strip()
            if not text:
                continue
            role = item.get("role", "user")
            if role not in ["user", "model"]:
                role = "user"
            
            if sanitized and sanitized[-1]["role"] == role:
                sanitized[-1]["parts"][0] += f"\n\n{text}"
            else:
                sanitized.append({"role": role, "parts": [text]})
        
        while sanitized and sanitized[0]["role"] != "user":
            sanitized.pop(0)
            
        return sanitized

    def send_message(self, message: str, chat_id: int):
        """대화 히스토리를 유지하며 AI와 대화합니다."""
        history = self.dynamo_mgr.get_chat_history(chat_id) if self.dynamo_mgr else []
        gemini_history = self._sanitize_gemini_history(history)

        try:
            chat_session = self.chat_model.start_chat(history=gemini_history)
            response = chat_session.send_message(message)
            self._record_usage(response)
            reply_text = response.text.strip()
        except Exception as e:
            logger.error(f"Error in Gemini send_message: {e}")
            # 만약 히스토리 충돌 등의 문제라면 히스토리 없이 단독 생성 시도
            fallback_response = self.chat_model.generate_content(message)
            self._record_usage(fallback_response)
            reply_text = fallback_response.text.strip()

        history.append({"role": "user", "text": message})
        history.append({"role": "model", "text": reply_text})
        if self.dynamo_mgr:
            self.dynamo_mgr.save_chat_history(chat_id, history)

        return reply_text


    def _get_history_text(self, chat_id: int):
        history = self.dynamo_mgr.get_chat_history(chat_id) if self.dynamo_mgr else []
        if not history:
            return ""
        lines = []
        for item in history:
            role = "사용자" if item.get("role") == "user" else "AI"
            text = item.get("text", "")
            lines.append(f"[{role}]: {text}")
        return "\n\n".join(lines)

    def generate_title_from_history(self, chat_id: int):
        history_text = self._get_history_text(chat_id)
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
        title = response.text.strip().strip('"').strip("'").strip()
        return title if title else "대화 기록"

    def generate_blog_post_from_history(self, title_instruction: str, chat_id: int):
        history_text = self._get_history_text(chat_id)
        if not history_text:
            return "대화 기록이 없습니다. 먼저 대화를 나누어 주세요."

        kst_now = datetime.now(ZoneInfo("Asia/Seoul"))

        prompt = f"""
# 역할 (Role & Anti-Slop Philosophy)
당신은 현업 시스템 및 클라우드 엔지니어링 10년 차 수석 테크니컬 에디터입니다.
당신의 목표는 CS/메일/대화 기록을 **'AI Slop(군더더기, 상투어, 억지 구조)이 0%인 순도 높은 실전 트러블슈팅 및 엔지니어링 기술 문서'**로 탈바꿈시키는 것입니다.

# [CRITICAL] 5대 슬롭 방지 규칙 (Zero-Slop Iron Rules)
1. **금지어 및 상투적 패턴 (Negative Constraints)**:
   - "이 트러블슈팅 로그는 ~하기 위해 작성되었다", "최근 ~의 발전으로 인해", "디지털 환경에서"
   - "결론적으로,", "요약하자면,", "궁극적으로,", "~인지하는 것이 얼마나 중요한지 보여주었다"
   - 위와 같은 AI 특유의 미사여구나 도덕 교과서식 훈계조 문장을 절대 사용하지 마세요.
2. **1줄 요약 팁 박스 필수 (::: tip)**:
   - H1 제목 바로 아래에 문제와 핵심 원인/해결책을 1~2문장으로 압축한 `::: tip 1줄 요약` 컨테이너를 반드시 삽입하세요.
3. **1차 팩트 중심 전개 (No Hallucination)**:
   - [대화 기록]에 등장하는 실제 에러 메시지, 로그, 설정값, 명령어를 글의 중심에 두세요.
   - 대화에 없는 가상의 에러 코드나 허위 명령어를 소설처럼 날조하지 마세요.
4. **결론 포맷 (핵심 체크포인트 / Gotchas)**:
   - 글의 마지막은 장황한 감상이 아닌, 실무자가 동일 상황에서 기억해야 할 **'## 핵심 체크포인트 (Gotchas)'** 2~3개 불렛포인트로 명쾌하게 마무리하세요.
5. **SEO 키워드 억지 볼드체 금지**:
   - 키워드를 인위적으로 볼드 처리하지 말고, 자연스러운 기술 용어로 서술하세요.

# 출력 형식 (Output Format)
- 인사말이나 부연 설명 없이 마크다운 텍스트만 출력하세요.
- **[매우 중요]: 절대로 결과물 전체를 감싸는 ```yaml 이나 ```markdown 등 코드 블록 선언을 하지 마세요. 출력은 반드시 `---` 기호로 시작해야 합니다.**
- **Frontmatter 요구사항**:
  - `title`: 명확하고 직관적인 글 제목 (큰따옴표 필수)
  - `description`: 글의 핵심 내용과 해결 과정을 요약한 1~2문장의 설명 (큰따옴표 필수)
  - `date`: {kst_now.strftime('%Y-%m-%d')}
  - `tags`: 본문 주제에 맞추어 태그 풀(`Tech, Troubleshooting, AWS, Cloud, Security, Incident Report, Forensic, Serverless, Lambda, Linux, Windows, WSL, Database, MySQL, MSSQL, Architecture, Network, Performance, Automation, VitePress, AI`)에서 3~5개 선택

---
title: "{title_instruction if title_instruction else '트러블슈팅 기록'}"
description: "글의 핵심 문제와 해결 과정을 간결히 요약한 1~2문장"
date: {kst_now.strftime('%Y-%m-%d')}
tags: [Tech, Troubleshooting, AWS, Linux]
---

# {title_instruction if title_instruction else '트러블슈팅 기록'}

::: tip 1줄 요약
문제 현상과 핵심 원인 및 해결 조치를 1~2문장으로 압축
:::

(본문 내용: 인시던트 개요 -> 원인 분석 -> 실전 복구/명령어 -> ## 핵심 체크포인트)

---
*게시된 시간: {kst_now.strftime('%Y-%m-%d %H:%M:%S')}*

[대화 기록]
{history_text}
"""
        response = self._generate_content_with_tracking(prompt)
        return response.text.strip()

    def generate_blog_from_news(self, news_data, user_comment):
        kst_now = datetime.now(ZoneInfo("Asia/Seoul"))
        prompt = f"""
당신은 기술 및 산업 트렌드를 날카롭게 분석하는 전문 테크 칼럼니스트입니다.
아래의 [뉴스 원본]과 [나의 코멘트]를 결합하여, 불필요한 미사여구(AI Slop) 없이 실질적인 인사이트를 전달하는 콤팩트한 마크다운 칼럼을 작성해 주세요.

[뉴스 원본]
- 제목: {news_data.get('title')}
- 링크: {news_data.get('url')}

[나의 코멘트]
{user_comment}

[작성 지침]
1. **슬롭 금지**: 뻔한 거시적 서두("현대 IT 사회에서...", "급변하는 시대에...") 및 상투적 결론("~는 중요한 시사점을 던져준다")을 철저히 배제하세요.
2. **사용자 코멘트 중심**: 뉴스는 핵심 사실만 2~3문장으로 압축하고, 글의 70% 이상을 [나의 코멘트]에 담긴 실무자적 시각과 해석에 집중하여 전개하세요.
3. **포맷**:
   - 최상단 Frontmatter (`title`, `description`, `date`, `tags`)
   - H1 헤딩 직후 `::: tip 핵심 인사이트` 박스 포함
   - 마지막 줄은 원문 링크 및 타임스탬프

---
title: "직관적이고 명확한 제목"
description: "핵심 인사이트 요약 1~2문장"
date: {kst_now.strftime('%Y-%m-%d')}
tags: [Blog, Tech, {news_data.get('category', 'Trend')}]
---

# 직관적이고 명확한 제목

::: tip 핵심 인사이트
칼럼의 핵심 주장 및 관점 요약
:::

(본문 내용)

---
*원문 출처: [{news_data.get('title')}]({news_data.get('url')})*
*게시된 시간: {kst_now.strftime('%Y-%m-%d %H:%M:%S')}*
"""
        response = self._generate_content_with_tracking(prompt)
        return response.text.strip()

    def translate_blog_post(self, markdown_text, target_language):
        timestamp_label = "Posted:" if "eng" in target_language.lower() or "en" in target_language.lower() else ("投稿日時:" if "jap" in target_language.lower() or "ja" in target_language.lower() else "게시된 시간:")
        prompt = f"""
Translate the following technical blog post into natural, idiomatic **{target_language}** as used by professional software engineers (e.g., Qiita/Zenn style for Japanese, Hacker News style for English).

[Strict Rules]
1. Preserve all Frontmatter YAML structure (`title` and `description` translated inside quotes, `date` and `tags` unchanged).
2. Preserve all VitePress markdown containers (`::: tip`, `::: warning`, `::: info` etc.) and code blocks exactly.
3. Keep technical terminology natural in {target_language} (e.g., avoid stiff literal translations).
4. Output ONLY the translated markdown without greetings or code block fences surrounding the entire text.
5. Translate the final timestamp line to `*{timestamp_label} YYYY-MM-DD HH:mm:ss*`.

[Source Markdown]
{markdown_text}
"""
        response = self._generate_content_with_tracking(prompt, model=self.trans_model)
        return response.text.strip()
