import feedparser
import re
from collections import Counter
from datetime import datetime, timedelta

# 한국어 트렌딩 키워드 추출 시 무시할 불용어 리스트
KOREAN_STOPWORDS = {
    # 조사/어미
    "에서", "으로", "에게", "까지", "부터", "에는", "에도", "와의",
    "이번", "대한", "위해", "통해", "관련", "이후", "이전", "대해",
    # 일반적인 뉴스 상투어
    "것으로", "것이", "수도", "있는", "없는", "하는", "되는", "된다",
    "발표", "진행", "예정", "올해", "내년", "오늘", "최근", "현재",
    "해당", "사실", "가능", "결과", "상황", "문제", "의견",
    # 기타 고빈도 비핵심 단어
    "기자", "뉴스", "속보", "종합", "단독", "포토", "영상",
    "한국", "국내", "세계", "서울", "정부", "사회", "경제",
}

# 정치 뉴스 관련 금지어 리스트
POLITICAL_KEYWORDS = {"정치", "국회", "여당", "야당", "대통령", "국회의원", "의원", "총선", "대선", "선거", "국민의힘", "더불어민주당", "여야"}

class NewsScraper:
    def __init__(self):
        self.game_rss_url = "https://news.google.com/rss/search?q=%EA%B2%8C%EC%9E%84&hl=ko&gl=KR&ceid=KR:ko"
        self.headline_rss_url = "https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko"
        self.yahoo_jp_rss_url = "https://news.yahoo.co.jp/rss/topics/top-picks.xml"

    def fetch_game_news(self, limit=1):
        """'게임' 키워드로 검색된 최신 뉴스 가져오기"""
        feed = feedparser.parse(self.game_rss_url)
        news_list = []
        for entry in feed.entries[:limit]:
            news_list.append({
                "title": entry.title,
                "url": entry.link,
                "content": entry.get("description", ""),
                "category": "game_news"
            })
        return news_list

    def fetch_trending_news(self, limit=2, threshold=2):
        """
        주요 뉴스에서 많이 겹치는 키워드(N개 이상)가 있는 기사를 추출.
        정치 뉴스를 제외하고 중복 없는 주요 시사 뉴스를 가져옵니다.
        """
        feed = feedparser.parse(self.headline_rss_url)
        
        # 제목에서 2글자 이상의 단어만 추출
        word_counts = Counter()
        entries_by_word = {}
        
        for entry in feed.entries:
            title = entry.title
            content = entry.get("description", "")
            
            # 정치 관련 뉴스 제외
            combined_text = f"{title} {content}"
            if any(keyword in combined_text for keyword in POLITICAL_KEYWORDS):
                continue

            # 특수기호 제거 및 단어 분리
            clean_title = re.sub(r'[^\w\s]', '', title)
            words = clean_title.split()
            
            seen_words = set()
            for word in words:
                if len(word) >= 2 and word not in KOREAN_STOPWORDS:
                    seen_words.add(word)
            
            for word in seen_words:
                word_counts[word] += 1
                if word not in entries_by_word:
                    entries_by_word[word] = []
                entries_by_word[word].append(entry)
                
        # threshold 이상 등장한 단어 중 가장 많이 등장한 키워드를 트렌드로 선정
        trending_news_list = []
        added_links = set()
        
        for word, count in word_counts.most_common():
            if count >= threshold:
                # 해당 트렌드 키워드의 대표 기사(첫 번째 기사) 1개만 추출
                representative_entry = entries_by_word[word][0]
                if representative_entry.link not in added_links:
                    trending_news_list.append({
                        "title": representative_entry.title,
                        "url": representative_entry.link,
                        "content": representative_entry.get("description", ""),
                        "category": "trending_news",
                        "trend_keyword": word
                    })
                    added_links.add(representative_entry.link)
                
                if len(trending_news_list) >= limit:
                    break
                    
        return trending_news_list

    def fetch_japan_news(self, limit=2):
        """Yahoo Japan RSS를 통해 실제 일본 주요 시사 뉴스 가져오기"""
        feed = feedparser.parse(self.yahoo_jp_rss_url)
        news_list = []
        for entry in feed.entries[:limit]:
            news_list.append({
                "title": entry.title,
                "url": entry.link,
                "content": entry.get("description", ""),
                "category": "japan_news"
            })
        return news_list
