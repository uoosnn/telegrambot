# tg-bot-auto-translation

텔레그램 봇을 이용한 기술/게임 뉴스 자동 번역 및 블로그 포스팅 자동화 툴입니다.

## 🛠️ 기능 개요

- ** 자동화된 워크플로우**: 스케줄러를 통해 주기적으로 뉴스를 수집하고 블로그에 게시합니다.
- **AI 기반 번역**: Gemini API를 사용하여 기술 뉴스, 게임 뉴스 및 대화 내용을 한국어에서 일본어로 자동 번역합니다.
- **블로그 자동화**:
    - 주요 키워드 분석을 통해 트렌딩 뉴스를 감지합니다.
    - 뉴스 원본과 사용자의 코멘트를 기반으로 자연스러운 일본어 블로그 초안을 생성합니다.
    - 생성된 콘텐츠는 GitHub 레포지토리(`uoosnn.github.io`)에 자동으로 푸시됩니다.

## 🚀 설치 및 설정

### 사전 요구사항
- Python 3.9 이상
- GitHub 계정 및 레포지토리 (`uoosnn.github.io`)
- Google Gemini API Key

### 1. 종속성 설치

필요한 라이브러리를 설치합니다.

```bash
pip install -r requirements.txt
```

### 2. 환경 변수 설정

`.env` 파일을 생성하고 다음 정보를 입력합니다.

```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
GEMINI_API_KEY=your_gemini_api_key_here
```

## ⚙️ 구성

### 스케줄 설정

`bot_main.py`에서 `schedule_interval` 변수를 조정하여 뉴스를 수집할 빈도를 변경할 수 있습니다.

- `schedule.every().hour.do(auto_post)`: 1시간마다 실행
- `schedule.every().day.at("01:00").do(auto_post)`: 매일 01:00에 실행

### 뉴스 수집 설정

`news_scraper.py`에서 수집할 뉴스의 양을 조절할 수 있습니다.
- `fetch_game_news(limit=2)`: 게임 뉴스 2개
- `fetch_trending_news(threshold=3)`: 주요 키워드 기반 트렌드 뉴스 2개

## 🏃‍♂️ 실행 방법

### 개발 모드 (실시간 실행)

봇을 실시간으로 실행하여 테스트합니다.

```bash
python bot_main.py
```

### 자동 배포 모드

자동화된 배포 파이프라인을 사용하여 봇을 실행합니다.

```bash
./deploy.sh
```

## 🔄 주요 기능 상세

### 실시간 채팅 모드
텔레그램 봇을 통해 AI와 실시간으로 대화할 수 있습니다. 대화 기록을 기반으로 블로그 포스트를 생성할 수 있습니다.

### 뉴스와 블로그 포스트 자동화
봇은 정기적으로 뉴스를 스크래핑하고, 사용자가 승인하면 자동으로 블로그 초안을 생성합니다. 사용자는 텔레그램에서 간단한 코멘트를 추가하여 블로그 콘텐츠의 품질을 향상시킬 수 있습니다.

### 버전 관리 및 배포
모든 변경 사항은 로컬 Git 레포지토리(`uoosnn.github.io`)에 커밋되고 GitHub으로 푸시되어 GitHub Pages를 통해 자동으로 배포됩니다.

## 📚 학습 리소스

- [Google Gemini API 문서](https://ai.google.dev/gemini-api/docs)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)

## 🔐 보안 및 개인정보

- API 키와 텔레그램 토큰은 `.env` 파일에 저장되며 Git에 포함되지 않도록 `.gitignore`에 추가되어 있습니다.
- 민감한 정보는 환경 변수를 통해 관리됩니다.

## 📝 문제 해결

문제가 발생하면 `bot_main.py`의 로깅 설정을 확인하여 상세한 에러 메시지를 확인하세요.
