# 텔레그램 봇 및 AI 연동 설정 가이드

본 자동화 봇을 정상적으로 실행하기 위해서는 **텔레그램 봇 토큰**, **사용자 채팅 ID**, 그리고 **Gemini API 키**가 필요합니다.

## 1. 텔레그램 봇 토큰 발급 받기
1. 텔레그램 앱을 열고 검색창에 `@BotFather`를 검색하여 봇파더 채팅방에 들어갑니다.
2. `/newbot` 명령어를 입력하여 새로운 봇 생성을 시작합니다.
3. 봇의 이름(Name)을 입력합니다. (예: `My Blog Auto Bot`)
4. 봇의 유저네임(Username)을 입력합니다. 반드시 `bot`으로 끝나야 합니다. (예: `my_blog_auto_bot`)
5. 성공적으로 생성되면 `HTTP API Token` (예: `123456789:ABCDefghi...`)을 발급해 줍니다. 이를 복사해 둡니다.

## 2. 사용자 채팅 ID(Chat ID) 알아내기
봇이 다른 사람에게 메시지를 보내지 않고 **본인에게만** 메시지를 보내도록 하려면 본인의 Chat ID가 필요합니다.
1. 텔레그램에서 `@userinfobot`을 검색하여 시작(Start)을 누릅니다.
2. 봇이 나의 정보와 함께 `Id` (숫자로 된 고유 ID)를 알려줍니다. (예: `12345678`)
3. 이를 복사해 둡니다.

## 3. Gemini API 키 발급 받기
1. [Google AI Studio](https://aistudio.google.com/app/apikey) 에 접속하여 Google 계정으로 로그인합니다.
2. `Create API key` 버튼을 눌러 새로운 API 키를 발급 받습니다.
3. 생성된 키를 복사해 둡니다.

## 4. .env 파일 생성 및 환경 변수 설정
`telegram_bot` 디렉토리 안에 `.env` 파일을 생성하고 아래 양식에 맞게 복사해 둔 값들을 입력합니다.

```env
# /source/telegram_bot/.env

TELEGRAM_BOT_TOKEN="여기에_복사한_봇_토큰_입력"
TELEGRAM_CHAT_ID="여기에_복사한_채팅_ID_입력"
GEMINI_API_KEY="여기에_복사한_Gemini_API_키_입력"
TARGET_REPO_PATH="/source/uoosnn.github.io"
```

## 5. 의존성 패키지 설치
봇을 실행할 서버(현재 PC)에서 아래 명령어를 통해 필요한 파이썬 라이브러리를 설치합니다.

```bash
cd /source/telegram_bot
pip install -r requirements.txt
```

## 6. 봇 실행
```bash
python bot_main.py
```
위 과정을 완료하시면 봇이 24시간 동안 타겟 사이트를 감시하고 텔레그램으로 알림을 보내줍니다.
