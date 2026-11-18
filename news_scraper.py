import logging

logger = logging.getLogger(__name__)


class NewsScraper:
    """
    [비활성화됨] 일반 뉴스(게임/트렌딩/일본 뉴스) 수집 기능은 사용자 요청에 의해 비활성화되었습니다.
    보안 취약점(CVE) 자동 알림은 cve_scraper.py의 CVEScraper가 담당합니다.
    """

    def __init__(self):
        self.enabled = False

    def fetch_game_news(self, limit=1):
        logger.info("News scraping (game_news) is disabled.")
        return []

    def fetch_trending_news(self, limit=2, threshold=2):
        logger.info("News scraping (trending_news) is disabled.")
        return []

    def fetch_japan_news(self, limit=2):
        logger.info("News scraping (japan_news) is disabled.")
        return []
