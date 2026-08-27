import os
import glob
import re
import time
import argparse
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()

PROMPT_TECH_TEMPLATE = """
당신은 현업 10년 차 수석 시스템/인프라 엔지니어이자 테크니컬 에디터입니다.
아래 제공된 [기존 기술 블로그 문서]를 **"AI Slop(군더더기, 상투어, 억지 구조, 도덕책식 결론)이 0%인 순도 높은 실전 엔지니어링 문서"**로 리팩토링해 주세요.

# [CRITICAL] 5대 슬롭 제거 규칙
1. **금지어 및 상투적 문구 완전 삭제**:
   - "이 트러블슈팅 로그는 ~하기 위해 작성되었다", "최근 ~의 발전으로 인해", "디지털 환경에서", "현대 IT 환경에서"
   - "결론적으로,", "요약하자면,", "궁극적으로,", "~인지하는 것이 얼마나 중요한지 보여주었다", "매우 중요한 시사점을 던져준다"
   - 인위적으로 만든 자문자답형 인용구(`> "가설: ..."` 등) 삭제.
2. **H1 제목 바로 아래 `::: tip 1줄 요약` 필수 삽입**:
   - 문제 현상, 핵심 원인, 해결 조치를 1~2문장으로 압축한 VitePress 팁 컨테이너 삽입.
3. **불필요한 SEO 볼드체 제거**:
   - `**트러블슈팅**`, `**AWS SNS 주제 복구**` 등 키워드 억지 볼드 처리를 지우고 자연스러운 문맥으로 전환.
4. **1차 팩트 및 명령어/코드 보존 및 강화**:
   - 원문에 나오는 실제 에러 로그, 아키텍처, CLI 명령어(`aws`, `strace`, `curl`, `SQL` 등)를 글의 핵심으로 배치.
   - 복잡한 장애 흐름은 간단한 ASCII 다이어그램이나 단계별 리스트로 시각화.
5. **결론을 실무자용 `## 핵심 체크포인트 (Gotchas)`로 개편**:
   - 훈계조/감상평 대신, 실무자가 동일 상황에서 주의해야 할 핵심 주의사항(Gotchas) 2~3개로 콤팩트하게 마무리.

# 출력 형식
- 최상단 Frontmatter (`title`, `description`, `date`, `tags`)는 유지 및 개선.
- 부연 설명이나 인사말 없이 오직 마크다운 텍스트만 출력하세요. (```markdown 코드 블록 펜스로 감싸지 마세요)
- 글 마지막 줄에 원본의 타임스탬프(`*게시된 시간: ...*`) 유지.

[기존 기술 블로그 문서]
{content}
"""

PROMPT_BLOG_TEMPLATE = """
당신은 기술과 산업을 통찰력 있게 분석하는 전문 테크 에디터입니다.
아래 제공된 [기존 블로그 문서]에서 **"뻔하고 공허한 AI 수식어(ChatGPT 클리셰)를 걷어내고, 실무자 관점의 날카롭고 고밀도인 에세이/칼럼"**으로 리팩토링해 주세요.

# [CRITICAL] 슬롭 제거 규칙
1. **상투적 클리셰 완전 삭제**:
   - "ChatGPT와 같은 생성형 AI의 등장은 패러다임을 근본적으로 바꾸고...", "How가 아닌 What", "코더에서 크리에이터로"
   - "결론적으로,", "요약하자면,", "주목할 만한 발전을 이루었다" 등 어디서나 볼 수 있는 양산형 문구 전면 제거.
2. **실무적 깊이와 구체적 관점 부여**:
   - 모호한 거시 담론 대신, 실무 개발자와 엔지니어가 현업에서 체감하는 현실적인 고민과 해결 방안으로 서술.
3. **H1 헤딩 아래 `::: tip 핵심 인사이트` 삽입**:
   - 글의 핵심 메시지를 1~2문장으로 압축.
4. **출력 포맷**:
   - Frontmatter 유지. 마크다운 펜스 없이 마크다운 전문만 출력.

[기존 블로그 문서]
{content}
"""

def get_refactored_content(model, content: str, is_tech: bool) -> str:
    template = PROMPT_TECH_TEMPLATE if is_tech else PROMPT_BLOG_TEMPLATE
    prompt = template.format(content=content)
    response = model.generate_content(prompt)
    text = response.text.strip()
    
    # 만약 결과물이 ```markdown 또는 ``` 로 감싸져 있다면 제거
    if text.startswith("```markdown"):
        text = text[len("```markdown"):].strip()
    elif text.startswith("```"):
        text = text[len("```"):].strip()
    if text.endswith("```"):
        text = text[:-3].strip()
        
    return text

def main():
    parser = argparse.ArgumentParser(description="Zero-Slop Batch Refactor for Blog Posts")
    parser.add_argument("--file", type=str, help="Specific file to refactor (relative or absolute path)")
    parser.add_argument("--model", type=str, default="gemini-2.5-flash", help="Gemini model name")
    parser.add_argument("--category", choices=["tech", "blog", "all"], default="all", help="Category to process")
    args = parser.parse_args()

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY is not set in environment or .env file.")
        return

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(args.model)

    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "uoosnn.github.io"))
    
    if args.file:
        target_files = [os.path.abspath(args.file)]
    else:
        target_files = []
        if args.category in ["tech", "all"]:
            tech_files = glob.glob(os.path.join(base_dir, "tech", "*.md"))
            target_files.extend([f for f in tech_files if os.path.basename(f) != "index.md"])
        if args.category in ["blog", "all"]:
            blog_files = glob.glob(os.path.join(base_dir, "blog", "*.md"))
            target_files.extend([f for f in blog_files if os.path.basename(f) != "index.md"])

    print(f"=== Starting Zero-Slop Batch Refactoring ({len(target_files)} files) ===")
    print(f"Model: {args.model}")

    for idx, file_path in enumerate(target_files, 1):
        filename = os.path.basename(file_path)
        is_tech = "\\tech\\" in file_path or "/tech/" in file_path
        print(f"\n[{idx}/{len(target_files)}] Processing ({'TECH' if is_tech else 'BLOG'}): {filename}")

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                original_content = f.read()

            refactored = get_refactored_content(model, original_content, is_tech)

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(refactored)

            print(f"  -> Successfully refactored and saved.")
            time.sleep(1)  # Rate limit safety
        except Exception as e:
            print(f"  -> ERROR processing {filename}: {e}")

    print("\n=== Refactoring completed! ===")

if __name__ == "__main__":
    main()
