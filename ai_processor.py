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
# 역할 (Role)
당신은 고객 응대 메일이나 CS 기록을 분석하여, 이를 철저히 **'엔지니어 시점의 깊이 있는 기술 블로그(Troubleshooting Log)'**로 탈바꿈시키는 10년 차 시니어 테크니컬 에디터입니다.

# 데이터의 본질 및 변환 목표 (Critical Task)
- 입력되는 [대화 기록]은 **고객에게 상황을 안내하기 위해 작성된 메일/대화**입니다.
- **절대 메일 내용을 단순히 요약하거나 정돈하는 수준에 머무르지 마세요.**
- 고객 응대용 표현("불편을 드려 죄송합니다", "조치 완료되었습니다", "문의하신 내용" 등)은 완벽히 제거하고, 이 사건의 이면에 있는 **'기술적 원인'과 '엔지니어의 문제 해결 과정'**에만 집중하여 완전히 새로운 관점의 글을 작성하세요.

# 지시사항 (Instructions)
1. **관점 전환 (POV Shift)**:
   - "고객에게 이렇게 안내했다"가 아니라, **"시스템에서 이런 에러가 발생했고, 우리는 이렇게 디버깅해서 해결했다"**는 1인칭 개발팀/엔지니어의 관점(평어체: ~했다, ~이다)으로 서술하세요.
2. **기술적 딥다이브 (Why & How)**:
   - 메일에 단편적으로 적힌 조치 결과를 바탕으로 기술적 맥락을 확장하세요.
   - '어떤 원리로 장애가 발생했는지', '왜 이런 조치 방법을 선택했는지' 논리적으로 전개하세요.
3. **용어 일반화 및 표준화**:
   - 특정 고객사 이름은 익명 처리(예: `A사`, `특정 클라이언트`)하고, 벤더 종속적인 솔루션명(예: CDP 백업, Sophos 등)은 범용적인 기술 용어(백업, 안티바이러스 등)로 순화하세요.
4. **민감 정보 마스킹 및 치환**:
   - 도메인 이름이 나오는 경우 무조건 `example.com`으로 치환하세요.
   - IP 주소가 나오는 경우 무조건 `x.x.x.x`로 치환하세요. 만약 서로 다른 IP가 두 개 이상 등장한다면 `x.x.x.1`, `x.x.x.2`와 같이 숫자를 붙여 구분하세요.
   - 프로젝트 고유의 특이한(일반적이지 않은) 디렉토리 이름이 나오는 경우 `example_a`, `example_b` 등의 이름으로 변경하세요.
5. **코드 및 가독성 (Markdown)**:
   - 관련 설정값이나 명령어가 있다면 반드시 마크다운 코드 블록(```)으로 묶고, 인라인 코드(`)를 적극 활용하세요.
   - [대화 기록] 중에 마크다운 형태의 이미지 링크(`![...](/images/...)`)가 포함되어 있다면, 글의 흐름상 가장 적절한 위치에 해당 링크를 반드시 포함하여 시각적 이해도를 높이세요.
6. **분량 및 가독성 최적화**: 
   - 원본 대화 내용이 빈약하다면 억지로 길게 쓰지 말고 핵심만 간결히 요약하세요. 
   - 복잡한 단계나 설명은 번호 매기기(List)나 표(Table)를 적극 활용하여 가독성을 높이세요.
7. **SEO 최적화**: 트러블슈팅과 관련된 핵심 기술 키워드를 본문 내에 자연스럽게 3~4회 이상 배치하세요.
8. **엄격한 팩트 체크**: 글을 재구성하되, [대화 기록]에 없는 전혀 다른 기술적 사실이나 가짜 원인을 지어내지는 마세요.

# 출력 형식 (Output Format)
- 인사말이나 부연 설명 없이 마크다운 텍스트만 출력하세요.
- **[매우 중요]: 절대로 결과물 전체를 감싸는 ```yaml 이나 ```markdown 등 코드 블록 선언을 하지 마세요. 출력은 반드시 `---` 기호로 시작해야 합니다.**
- **[매우 중요]: `---` 프론트매터 블록이 끝난 바로 다음 줄(본문 최상단)에는 반드시 `# {title_instruction if title_instruction else '트러블슈팅 기록'}` 제목 헤딩을 주입하세요.**
- **Frontmatter 요구사항**:
  - `title`: 명확하고 간결한 글 제목 (큰따옴표 필수)
  - `description`: 글의 핵심 내용과 해결 과정을 요약한 1~2문장의 설명 (SEO 및 태그 카드 설명용, 큰따옴표 필수)
  - `date`: `{kst_now.strftime('%Y-%m-%d')}`
  - `tags`: 본문 주제에 맞추어 아래 태그 풀(Tag Pool) 중에서 가장 관련성 높은 3~5개를 배열 형태로 선택
    - 태그 풀: `Tech, Troubleshooting, AWS, Cloud, Security, Incident Report, Forensic, Serverless, Lambda, Linux, Windows, WSL, Database, MySQL, MSSQL, Architecture, Network, Performance, Automation, VitePress, AI`

---
title: "{title_instruction if title_instruction else '트러블슈팅 기록'}"
description: "글의 핵심 문제와 해결 과정을 간결히 요약한 1~2문장"
date: {kst_now.strftime('%Y-%m-%d')}
tags: [Tech, Troubleshooting, AWS, Linux]
---

# {title_instruction if title_instruction else '트러블슈팅 기록'}

문서의 가장 마지막 줄에는 아래 문구를 출력하세요.

---
*게시된 시간: {kst_now.strftime('%Y-%m-%d %H:%M:%S')}*

입력 데이터 (Input Data: 고객 안내 메일 기반)
[대화 기록]
{history_text}
"""
        response = self._generate_content_with_tracking(prompt)
        return response.text.strip()

    def generate_blog_from_news(self, news_data, user_comment):
        kst_now = datetime.now(ZoneInfo("Asia/Seoul"))
        prompt = f"""
당신은 기술 및 사회 트렌드를 깊이 있게 분석하는 전문 블로그 에디터입니다.
아래의 [뉴스 원본]과 [나의 코멘트]를 결합하여, 독자에게 인사이트를 제공하는 훌륭한 마크다운(Markdown) 포스트를 작성해 주세요.

[뉴스 원본]
- 제목: {news_data.get('title')}
- 링크: {news_data.get('url')}

[나의 코멘트]
{user_comment}

[작성 지침 및 요구사항]
1. **톤앤매너**: 블로그 독자를 대상으로 하는 전문적이고 분석적인 평어체(~다, ~이다, ~했다)를 사용하세요.
2. **구조와 융합**: 기계적으로 '뉴스 요약'과 '내 생각'을 분리하지 마세요. 뉴스 핵심 내용을 요약하면서, 사용자의 코멘트를 자연스럽게 본문에 녹여내어 하나의 완성된 칼럼처럼 작성하세요. 마지막에는 원문 링크를 포함한 결론으로 마무리하세요.
3. **가독성 및 SEO**: 
   - 내용이 길어질 경우 소제목(##)과 글머리 기호(-), 또는 표(Table)를 적절히 활용하세요.
   - 검색 엔진 최적화(SEO)를 위해, 글의 핵심 키워드를 본문에 자연스럽게 3~4회 이상 배치하세요.
4. **팩트 준수**: 제공된 [뉴스 원본]과 [나의 코멘트] 범위를 벗어난 거짓 정보(할루시네이션)를 지어내지 마세요.
5. **포맷팅 강제**:
   - 최상단에 반드시 아래 형식의 Frontmatter를 포함하세요.
     ---
     title: "매력적인 제목" (양끝 큰따옴표 필수)
     description: "뉴스 핵심 내용과 인사이트를 요약한 1~2문장" (양끝 큰따옴표 필수)
     date: {kst_now.strftime('%Y-%m-%d')}
     tags: [Blog, 뉴스, {news_data.get('category', 'Trend')}]
     ---
   - `---` 프론트매터 직후 본문 최상단 첫 줄에는 반드시 `# 매력적인 제목` H1 헤딩을 삽입하세요.
   - 부연 설명이나 인사말 없이 오직 마크다운 텍스트만 출력하세요.
   - 글의 가장 마지막 줄에는 반드시 아래 문구를 추가하세요.
     ---
     *게시된 시간: {kst_now.strftime('%Y-%m-%d %H:%M:%S')}*
"""
        response = self._generate_content_with_tracking(prompt)
        return response.text.strip()

    def translate_blog_post(self, markdown_text, target_language):
        timestamp_label = "Posted:" if "eng" in target_language.lower() or "en" in target_language.lower() else ("投稿日時:" if "jap" in target_language.lower() or "ja" in target_language.lower() else "게시된 시간:")
        prompt = f"""
당신은 완벽한 IT/블로그 전문 번역가입니다.
아래 제공된 [원본 마크다운] 문서를 **{target_language}**로 번역해 주세요.

[요구사항]
1. 최상단의 Frontmatter(yaml 형식) 블록에서 `title`과 `description`의 값은 해당 언어로 번역하고 **반드시 큰따옴표("")**로 묶어주세요. (`date`와 `tags`는 원본과 동일하게 유지)
2. `---` 프론트매터 바로 다음 줄에 나오는 `# [제목]` 헤딩도 자연스러운 **{target_language}**로 번역하세요.
3. 마크다운의 구조(코드 블록, 인용구, 링크, 볼드체 등)는 절대 훼손하지 마세요.
4. 글 하단의 타임스탬프 문구인 `*게시된 시간: YYYY-MM-DD HH:mm:ss*`는 **`*{timestamp_label} YYYY-MM-DD HH:mm:ss*`** 형식으로 정합성을 맞추어 번역하세요.
5. 글의 내용과 맥락을 가장 자연스럽고 매끄러운 {target_language}로 번역하세요.
6. 오직 번역된 마크다운 결과물만 출력하세요. (설명이나 인사말 금지)

[원본 마크다운]
{markdown_text}
"""
        response = self._generate_content_with_tracking(prompt, model=self.trans_model)
        return response.text.strip()
