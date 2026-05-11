import requests
import json
import os
import logging

logger = logging.getLogger(__name__)

class BaseScraper:
    """확장 가능한 웹 크롤러의 기본 클래스"""
    def __init__(self, name, site_name, category):
        self.name = name
        self.site_name = site_name
        self.category = category
        self.last_post_id = None
        self.load_last_post()

    def get_last_post_path(self):
        return f"data/last_post_{self.name}.json"

    def load_last_post(self):
        path = self.get_last_post_path()
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.last_post_id = data.get('last_post_id')

    def save_last_post(self, post_id):
        self.last_post_id = post_id
        path = self.get_last_post_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'last_post_id': post_id}, f)

    def fetch_new_posts(self):
        """
        새로운 게시글 목록을 반환합니다.
        가장 오래된 새 글부터 가장 최신 글 순서로 반환해야 알림이 올바른 순서로 전송됩니다.
        리턴 양식: [{'id': str, 'title': str, 'content': str, 'url': str, 'site_name': str, 'category': str}]
        """
        raise NotImplementedError


class NaverLoungeScraper(BaseScraper):
    """네이버 게임 라운지 크롤러"""
    def __init__(self, lounge_id="nikke", board_id="15"):
        # lounge_id: 라운지 영문 ID (예: nikke)
        # board_id: 게시판 ID (예: 15 - 보통 공지사항)
        super().__init__(name=f"naver_{lounge_id}_{board_id}", site_name=f"네이버 라운지({lounge_id})", category="공지사항")
        self.lounge_id = lounge_id
        self.board_id = board_id
        self.api_url = f"https://comm-api.game.naver.com/nng_main/v1/lounges/{self.lounge_id}/boards/{self.board_id}/posts"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

    def fetch_new_posts(self):
        try:
            params = {'limit': 10}
            response = requests.get(self.api_url, headers=self.headers, params=params)
            response.raise_for_status()
            data = response.json()
            
            posts = data.get('content', [])
            if not posts:
                return []

            new_posts = []
            
            # API는 보통 최신 글이 먼저 옴.
            for post in posts:
                post_id = str(post.get('feedId'))
                # 만약 기존에 확인했던 글이면 그만 탐색
                if self.last_post_id and post_id == self.last_post_id:
                    break
                    
                title = post.get('title', '')
                content = post.get('contents', '')
                # 본문이 너무 길면 자르기 (상세 본문은 다른 API를 호출해야 할 수도 있지만 프리뷰 활용)
                url = f"https://game.naver.com/lounge/{self.lounge_id}/board/{self.board_id}/detail/{post_id}"
                
                new_posts.append({
                    'id': post_id,
                    'title': title,
                    'content': content,
                    'url': url,
                    'site_name': self.site_name,
                    'category': self.category
                })
            
            # 리스트를 역순으로 정렬하여 (오래된 새글 -> 최신글) 반환
            new_posts.reverse()
            
            if new_posts:
                self.save_last_post(new_posts[-1]['id'])
                
            return new_posts

        except Exception as e:
            logger.error(f"Error fetching Naver Lounge posts: {e}")
            return []
