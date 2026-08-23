import os
import re
import logging
import urllib.parse
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

    def _extract_frontmatter_info(self, markdown_text):
        fm_match = re.match(r'^---\r?\n([\s\S]*?)\r?\n---', markdown_text.strip())
        if not fm_match:
            return {}
        fm_text = fm_match.group(1)
        title_m = re.search(r'^title:\s*["\']?([^"\'\n]+)["\']?', fm_text, re.MULTILINE)
        desc_m = re.search(r'^description:\s*["\']?([^"\'\n]+)["\']?', fm_text, re.MULTILINE)
        date_m = re.search(r'^date:\s*([^\n\r]+)', fm_text, re.MULTILINE)
        tags_m = re.search(r'^tags:\s*\[(.*?)\]', fm_text, re.MULTILINE)
        
        tags = [t.strip().strip('"').strip("'") for t in tags_m.group(1).split(',')] if tags_m else []
        
        return {
            "title": title_m.group(1).strip() if title_m else "",
            "description": desc_m.group(1).strip() if desc_m else "",
            "date": date_m.group(1).strip() if date_m else "",
            "tags": tags
        }

    def _determine_tech_category(self, tags, title=""):
        combined = " ".join(tags).lower() + " " + title.lower()
        if any(k in combined for k in ['serverless', 'lambda', 'architecture', 'automation', 'vitepress', 'bot', 'gemini', '파이프라인']):
            return 3  # Serverless & Architecture
        if any(k in combined for k in ['performance', 'lighthouse', 'npm', 'audit', 'devops', 'seo', 'accessibility', '접근성', '성능']):
            return 4  # Web Performance & DevOps
        if any(k in combined for k in ['aws', 'cloud', 'migration', 'backup', 'rds', 'ebs', 'ami', 'disk', 'network', 'forti', '마이그레이션']):
            return 1  # Cloud & Infrastructure
        return 2  # Troubleshooting & Security (Default)

    def _insert_row_into_index(self, index_content, category_num, title, filename, summary, date_str):
        cat_header = f"### {category_num}."
        pos = index_content.find(cat_header)
        if pos == -1:
            pos = index_content.find("### ")
            if pos == -1:
                return index_content

        table_sep = "|:---|:---|:---:|"
        sep_pos = index_content.find(table_sep, pos)
        if sep_pos == -1:
            return index_content
        
        encoded_link = f"./{urllib.parse.quote(filename)}"
        if filename in index_content or encoded_link in index_content:
            return index_content

        new_row = f"\n| [{title}]({encoded_link}) | {summary} | {date_str} |"
        insert_at = sep_pos + len(table_sep)
        updated_content = index_content[:insert_at] + new_row + index_content[insert_at:]
        return updated_content

    def _update_tech_index(self, filename, ko_content, translations=None):
        """tech/index.md 및 다국어 인덱스 파일에 신규 포스트 행 자동 삽입"""
        try:
            ko_meta = self._extract_frontmatter_info(ko_content)
            category_num = self._determine_tech_category(ko_meta.get("tags", []), ko_meta.get("title", ""))
            
            # 1. 한국어 tech/index.md 업데이트
            try:
                ko_index_file = self.repo.get_contents("tech/index.md", ref=self.branch)
                ko_index_content = ko_index_file.decoded_content.decode('utf-8')
                updated_ko_index = self._insert_row_into_index(
                    ko_index_content,
                    category_num,
                    ko_meta.get("title", filename.replace(".md", "")),
                    filename,
                    ko_meta.get("description", ko_meta.get("title", "")),
                    ko_meta.get("date", "")
                )
                if updated_ko_index != ko_index_content:
                    self._commit_or_update_file("tech/index.md", updated_ko_index, f"docs: Update tech/index.md for {filename}")
            except Exception as e:
                logger.warning(f"Failed to update tech/index.md: {e}")

            # 2. 영문 en/tech/index.md 업데이트
            if translations and "en" in translations:
                en_meta = self._extract_frontmatter_info(translations["en"])
                try:
                    en_index_file = self.repo.get_contents("en/tech/index.md", ref=self.branch)
                    en_index_content = en_index_file.decoded_content.decode('utf-8')
                    updated_en_index = self._insert_row_into_index(
                        en_index_content,
                        category_num,
                        en_meta.get("title", ko_meta.get("title", "")),
                        filename,
                        en_meta.get("description", en_meta.get("title", "")),
                        ko_meta.get("date", "")
                    )
                    if updated_en_index != en_index_content:
                        self._commit_or_update_file("en/tech/index.md", updated_en_index, f"docs: Update en/tech/index.md for {filename}")
                except Exception as ee:
                    logger.warning(f"Failed to update en/tech/index.md: {ee}")

            # 3. 일문 ja/tech/index.md 업데이트
            if translations and "ja" in translations:
                ja_meta = self._extract_frontmatter_info(translations["ja"])
                try:
                    ja_index_file = self.repo.get_contents("ja/tech/index.md", ref=self.branch)
                    ja_index_content = ja_index_file.decoded_content.decode('utf-8')
                    updated_ja_index = self._insert_row_into_index(
                        ja_index_content,
                        category_num,
                        ja_meta.get("title", ko_meta.get("title", "")),
                        filename,
                        ja_meta.get("description", ja_meta.get("title", "")),
                        ko_meta.get("date", "")
                    )
                    if updated_ja_index != ja_index_content:
                        self._commit_or_update_file("ja/tech/index.md", updated_ja_index, f"docs: Update ja/tech/index.md for {filename}")
                except Exception as je:
                    logger.warning(f"Failed to update ja/tech/index.md: {je}")

        except Exception as e:
            logger.error(f"Error in _update_tech_index: {e}", exc_info=True)

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

            # 3. tech 포스트인 경우 index.md (KO/EN/JA) 자동 업데이트
            if folder_name == "tech":
                self._update_tech_index(filename, formatted_ko, translations=translations)
                uploaded_paths.append("tech/index.md (자동 색인)")

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

