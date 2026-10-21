# 텔레그램 봇 AWS Lambda 서버리스 배포 및 설정 가이드

본 가이드는 상시 가동 서버 없이 **100% 무료(0원)**로 텔레그램 봇을 AWS Lambda + DynamoDB + EventBridge 환경에서 운영하는 단계별 절차입니다.

---

## 📌 주요 구성 요소
- **컴퓨팅**: AWS Lambda (Python 3.11/3.12, 월 100만 회 영구 무료)
- **엔드포인트**: Lambda Function URL (추가 과금 없는 무료 HTTPS 엔드포인트)
- **데이터베이스**: Amazon DynamoDB (`telegram_bot_state`, 25GB 영구 무료)
- **스케줄러**: Amazon EventBridge (매일 아침 09:00 KST 뉴스 스크래핑 트리거, 무료)
- **블로그 업로더**: GitHub REST API (`PyGithub`, `uoosnn/uoosnn.github.io` 자동 커밋)

---

## 🛠️ 배포 및 설정 단계

### 1단계: 배포 패키지 빌드 (`lambda_deployment.zip`)
프로젝트 폴더(`c:\Users\uoosnn\Documents\DEV\telegrambot`)에서 아래 스크립트를 실행합니다:
```bash
python build_zip.py
```
실행이 완료되면 루트에 **`lambda_deployment.zip`** 파일이 생성됩니다.

---

### 2단계: AWS DynamoDB 테이블 생성
1. [AWS 콘솔 DynamoDB](https://console.aws.amazon.com/dynamodbv2/) 접속 (Region: `ap-northeast-2` 서울)
2. **테이블 생성** 클릭:
   - **테이블 이름**: `telegram_bot_state`
   - **파티션 키 (PK)**: `PK` (String)
   - **정렬 키 (SK)**: `SK` (String)
   - **테이블 설정**: 온디맨드(On-Demand) 또는 프로비저닝(1 RCU / 1 WCU)
3. **생성** 클릭

---

### 3단계: AWS Lambda 함수 설정 및 코드 업로드
1. [AWS 콘솔 Lambda](https://console.aws.amazon.com/lambda/) 접속 (Region: `ap-northeast-2` 서울)
2. `telegram-bot` 함수 선택 (또는 신규 생성: Python 3.11/3.12)
3. **코드(Code)** 탭 ➔ **[업로드(Upload from)]** ➔ **[.zip 파일]** ➔ 1단계에서 생성된 `lambda_deployment.zip` 선택 후 저장
4. **구성(Configuration) ➔ 일반 구성(General configuration)**:
   - 제한 시간(Timeout): **1분 30초 (90초)**
   - 메모리: **256MB** 또는 **512MB**
5. **구성(Configuration) ➔ 권한(Permissions)**:
   - 실행 역할(Role)에 DynamoDB 및 비동기 자기 호출(Self-Invocation) 권한 연결:
     ```json
     {
         "Version": "2012-10-17",
         "Statement": [
             {
                 "Sid": "DynamoDBAccess",
                 "Effect": "Allow",
                 "Action": ["dynamodb:*"],
                 "Resource": "*"
             },
             {
                 "Sid": "AllowLambdaSelfInvoke",
                 "Effect": "Allow",
                 "Action": ["lambda:InvokeFunction"],
                 "Resource": "*"
             }
         ]
     }
     ```
6. **구성(Configuration) ➔ 환경 변수(Environment variables)**:
   - `TELEGRAM_BOT_TOKEN`: 텔레그램 봇 토큰
   - `TELEGRAM_CHAT_ID`: 내 텔레그램 채팅 ID
   - `GEMINI_API_KEY`: Google Gemini API 키
   - `GITHUB_TOKEN`: GitHub Personal Access Token (PAT, `repo` 권한)
   - `TARGET_REPO`: `uoosnn/uoosnn.github.io`
   - `DYNAMODB_TABLE`: `telegram_bot_state`
7. **구성(Configuration) ➔ 함수 URL(Function URL)**:
   - 활성화 여부 확인: `https://7kpqtljrkmy3cizcjteqgdptwm0pjexk.lambda-url.ap-northeast-2.on.aws/` (인증 유형: NONE)

---

### 4단계: EventBridge 아침 9시 스케줄러 연결
1. `telegram-bot` 함수 페이지 상단 다이어그램에서 **[+ 트리거 추가 (Add trigger)]** 클릭
2. **EventBridge (CloudWatch Events)** 선택
3. **새 규칙 생성** 선택:
   - 규칙 이름: `telegram-news-daily-9am`
   - 규칙 유형: **일정 표현식 (Schedule expression)**
   - 일정 표현식: `cron(0 0 * * ? *)` *(UTC 00:00 = 한국 시간 KST 09:00)*
4. **[추가 (Add)]** 클릭

---

### 5단계: 텔레그램 Webhook 등록 (대기열 초기화 포함)
웹 브라우저 주소창이나 curl로 아래 URL을 1회 호출합니다 (`drop_pending_updates=true`를 붙이면 밀려있던 중복 웹훅 대기열이 즉시 비워집니다):
```text
https://api.telegram.org/bot<내_TELEGRAM_BOT_TOKEN>/setWebhook?url=https://7kpqtljrkmy3cizcjteqgdptwm0pjexk.lambda-url.ap-northeast-2.on.aws/&drop_pending_updates=true
```
응답으로 `{"ok":true,"result":true,"description":"Webhook was set"}`가 나오면 완료입니다.
