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
            temperature=0.6,
            top_p=0.8,
            top_k=40
        )
        self.post_model = genai.GenerativeModel(
            'gemini-2.5-pro',
            generation_config=post_config,
            system_instruction="당신은 사실 기반으로만 글을 작성하는 정확한 블로그 에디터입니다. 제공받은 정보 외에 임의의 사실을 절대 지어내지 마세요. 추측이나 불확실한 내용은 포함하지 마세요."
        )
        
        # 단순 번역용 가성비 모델: 저렴하고 빠른 gemini-2.5-flash 사용
        trans_config = genai.types.GenerationConfig(
            temperature=0.3, # 번역은 창의성보다 정확성이 중요하므로 온도를 낮춤
            top_p=0.8,
            top_k=40
        )
        self.trans_model = genai.GenerativeModel(
            'gemini-2.5-flash',
            generation_config=trans_config,
            system_instruction="당신은 원본의 형식과 마크다운 문법을 완벽히 보존하는 전문 번역가입니다."
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
4. **코드 및 가독성 (Markdown)**:
   - 관련 설정값이나 명령어가 있다면 반드시 마크다운 코드 블록(```)으로 묶고, 인라인 코드(`)를 적극 활용하세요.
   - [대화 기록] 중에 마크다운 형태의 이미지 링크(`![...](/images/...)`)가 포함되어 있다면, 글의 흐름상 가장 적절한 위치(예: 에러 원인 설명부, 해결 후 화면 등)에 해당 링크를 반드시 포함하여 시각적 이해도를 높이세요.
5. **엄격한 팩트 체크**: 글을 재구성하되, [대화 기록]에 없는 전혀 다른 기술적 사실이나 가짜 원인을 지어내지는 마세요.

# 출력 형식 (Output Format)
- 인사말이나 부연 설명 없이 마크다운 텍스트만 출력하세요.

---
title: "{title_instruction if title_instruction else '트러블슈팅 기록'}"
date: {kst_now.strftime('%Y-%m-%d')}
tags: [Tech, Troubleshooting, Incident Report]
---
문서의 가장 마지막 줄에는 아래 문구를 출력하세요.

게시된 시간: {kst_now.strftime('%Y-%m-%d %H:%M:%S')}

입력 데이터 (Input Data: 고객 안내 메일 기반)
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
        # 번역에는 훨씬 저렴하고 빠른 trans_model (gemini-2.5-flash) 사용
        response = self._generate_content_with_tracking(prompt, model=self.trans_model)
        return response.text.strip()
