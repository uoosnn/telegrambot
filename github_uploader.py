import os
import re
import logging
from github import Github, GithubException, Auth

logger = logging.getLogger(__name__)

class GithubUploader:
    def __init__(self, token=None, repo_name=None):
        self.token = token or os.getenv("GITHUB_TOKEN")
        self.repo_name = repo_name or os.getenv("TARGET_REPO", "uoosnn/uoosnn.github.io")
        self.branch = os.getenv("TARGET_BRANCH", "main")
        
        if not self.token:
            logger.warning("GITHUB_TOKEN is not set.")
            self.github = None
            self.repo = None
        else:
            try:
                auth = Auth.Token(self.token)
                self.github = Github(auth=auth)
                self.repo = self.github.get_repo(self.repo_name)
            except Exception as e:
                logger.error(f"Failed to initialize Github client: {e}")
                self.github = None
                self.repo = None

    def generate_filename(self, title):
        clean_title = re.sub(r'[\\/*?:"<>|]', "", title)
        clean_title = clean_title[:50].strip()
        return f"{clean_title}.md"

    def _clean_markdown(self, text):
        text = text.strip()
        if text.startswith("```markdown"):
            text = text[11:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        return text.strip()

    def _format_markdown_for_lang(self, text, lang="ko"):
        text = self._clean_markdown(text)
        
        fm_match = re.match(r'^---\r?\n([\s\S]*?)\r?\n---', text)
        if not fm_match:
            return text
        
        fm_text = fm_match.group(1).strip()
        title_match = re.search(r'^title:\s*["\']?([^"\'\n]+)["\']?', fm_text, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else ""
        
        body = text[fm_match.end():].strip()
        
        if title:
            if body.startswith('# '):
                body = re.sub(r'^#\s+[^\r\n]+', f'# {title}', body)
            else:
                body = f'# {title}\n\n' + body
        
        timestamp_label = "게시된 시간:"
        if lang == "en":
            timestamp_label = "Posted:"
        elif lang == "ja":
            timestamp_label = "投稿日時:"
            
        body = re.sub(r'\*?(게시된 시간|Posted|投稿日時|投稿時間):\s*', f'*{timestamp_label} ', body)

        return f"---\n{fm_text}\n---\n\n{body}\n"

    def _commit_or_update_file(self, path, content, commit_message):
        """GitHub REST API로 파일 생성 또는 업데이트"""
        if not self.repo:
            raise Exception("GitHub repository is not configured.")
            
        try:
            # 기존 파일 확인
            try:
                existing = self.repo.get_contents(path, ref=self.branch)
                self.repo.update_file(
                    path=path,
                    message=commit_message,
                    content=content,
                    sha=existing.sha,
                    branch=self.branch
                )
                logger.info(f"Updated file on GitHub: {path}")
            except GithubException as ge:
                if ge.status == 404:
                    self.repo.create_file(
                        path=path,
                        message=commit_message,
                        content=content,
                        branch=self.branch
                    )
                    logger.info(f"Created file on GitHub: {path}")
                else:
                    raise ge
            return True
        except Exception as e:
            logger.error(f"Error committing file {path} to GitHub: {e}")
            raise e

    def save_and_push(self, markdown_content, title=None, folder_name="blog", translations=None):
        if not self.repo:
            return False, "GitHub repository connection not configured (check GITHUB_TOKEN)."

        if not title:
            title_match = re.search(r'title:\s*["\']?(.*?)["\']?(\n|$)', markdown_content)
            title = title_match.group(1).strip() if title_match else "새로운_포스트"

        filename = self.generate_filename(title)
        filepath = f"{folder_name}/{filename}"
        formatted_ko = self._format_markdown_for_lang(markdown_content, lang="ko")

        try:
            # 1. 한국어 원본 커밋
            commit_msg = f"docs: Auto post from Telegram Bot ({title})"
            self._commit_or_update_file(filepath, formatted_ko, commit_msg)
            uploaded_paths = [filepath]

            # 2. 다국어 번역본 커밋
            if translations:
                for lang, trans_content in translations.items():
                    lang_path = f"{lang}/{folder_name}/{filename}"
                    formatted_trans = self._format_markdown_for_lang(trans_content, lang=lang)
                    trans_commit_msg = f"docs: Auto translate {lang} ({title})"
                    self._commit_or_update_file(lang_path, formatted_trans, trans_commit_msg)
                    uploaded_paths.append(lang_path)

            return True, f"GitHub ({', '.join(uploaded_paths)})"
        except Exception as e:
            return False, str(e)

    def save_image_and_push(self, file_bytes, filename):
        if not self.repo:
            return False, "GitHub repository connection not configured (check GITHUB_TOKEN)."

        filepath = f"public/images/{filename}"
        commit_msg = f"docs: Upload image {filename}"

        try:
            self._commit_or_update_file(filepath, file_bytes, commit_msg)
            return True, f"![이미지](/images/{filename})"
        except Exception as e:
            return False, str(e)
