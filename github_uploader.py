import os
import re
import logging
import urllib.parse
from github import Github, GithubException, Auth, InputGitTreeElement

logger = logging.getLogger(__name__)

class GithubUploader:
    def __init__(self, token=None, repo_name=None):
        self.token = token or os.getenv("GITHUB_TOKEN")
        self.repo_name = repo_name or os.getenv("TARGET_REPO", "uoosnn/uoosnn.github.io")
        self.branch = os.getenv("TARGET_BRANCH", "main")
        self.last_uploaded = None
        
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

    def _remove_row_from_index(self, index_content: str, filename: str) -> str:
        """index.md 테이블에서 삭제/비공개된 파일명 링크가 포함된 행을 제거"""
        encoded_link = f"./{urllib.parse.quote(filename)}"
        raw_link = f"./{filename}"
        lines = index_content.splitlines()
        filtered_lines = [
            line for line in lines
            if not (
                line.strip().startswith("|")
                and (encoded_link in line or raw_link in line or f"({filename})" in line)
            )
        ]
        res = "\n".join(filtered_lines)
        if index_content.endswith("\n") and not res.endswith("\n"):
            res += "\n"
        return res

    def _prepare_tech_index_updates(self, filename, ko_content, translations=None) -> dict:
        """tech/index.md 및 다국어 인덱스 파일의 업데이트 본문을 딕셔너리로 반환"""
        index_updates = {}
        try:
            ko_meta = self._extract_frontmatter_info(ko_content)
            category_num = self._determine_tech_category(ko_meta.get("tags", []), ko_meta.get("title", ""))

            # 1. 한국어 tech/index.md
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
                    index_updates["tech/index.md"] = updated_ko_index
            except Exception as e:
                logger.warning(f"Failed to read/prepare tech/index.md: {e}")

            # 2. 영문 en/tech/index.md
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
                        index_updates["en/tech/index.md"] = updated_en_index
                except Exception as ee:
                    logger.warning(f"Failed to read/prepare en/tech/index.md: {ee}")

            # 3. 일문 ja/tech/index.md
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
                        index_updates["ja/tech/index.md"] = updated_ja_index
                except Exception as je:
                    logger.warning(f"Failed to read/prepare ja/tech/index.md: {je}")

        except Exception as e:
            logger.error(f"Error in _prepare_tech_index_updates: {e}", exc_info=True)
        return index_updates

    def _prepare_tech_index_removals(self, filename: str) -> dict:
        """tech/index.md (KO/EN/JA)에서 삭제/비공개된 포스트 행을 제거한 본문 딕셔너리 반환"""
        index_updates = {}
        for index_path in ["tech/index.md", "en/tech/index.md", "ja/tech/index.md"]:
            try:
                idx_file = self.repo.get_contents(index_path, ref=self.branch)
                idx_content = idx_file.decoded_content.decode("utf-8")
                updated_content = self._remove_row_from_index(idx_content, filename)
                if updated_content != idx_content:
                    index_updates[index_path] = updated_content
            except Exception as e:
                logger.warning(f"Failed to prepare index removal for {index_path}: {e}")
        return index_updates

    def _update_tech_index(self, filename, ko_content, translations=None):
        """tech/index.md 및 다국어 인덱스 파일에 신규 포스트 행 자동 삽입 (개별 호출 하위 호환용)"""
        updates = self._prepare_tech_index_updates(filename, ko_content, translations=translations)
        for path, content in updates.items():
            try:
                self._commit_or_update_file(path, content, f"docs: Update {path} for {filename}")
            except Exception as e:
                logger.warning(f"Failed to update {path}: {e}")

    def _extract_image_paths(self, markdown_text: str) -> list:
        """마크다운 본문에서 /images/img_... 경로 추출"""
        if not markdown_text:
            return []
        matches = re.findall(r'\(/images/([^)\s"\']+)', markdown_text)
        return list(dict.fromkeys([f"public/images/{m}" for m in matches if m]))

    def _commit_or_update_file(self, path, content, commit_message):
        """GitHub REST API로 단일 파일 생성 또는 업데이트"""
        if not self.repo:
            raise Exception("GitHub repository is not configured.")
            
        try:
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

    def _commit_multiple_changes(self, updates: dict, deletes: list, commit_message: str):
        """
        Git Tree API를 사용하여 여러 파일의 생성/수정(updates) 및 삭제(deletes)를
        단 1번의 원자적 커밋(Single Atomic Commit)으로 반영합니다.
        """
        if not self.repo:
            raise Exception("GitHub repository is not configured.")
        if not updates and not deletes:
            return False

        try:
            ref = self.repo.get_git_ref(f"heads/{self.branch}")
            latest_commit_sha = ref.object.sha
            base_commit = self.repo.get_git_commit(latest_commit_sha)
            base_tree = base_commit.tree

            tree_elements = []
            for path, content in (updates or {}).items():
                tree_elements.append(
                    InputGitTreeElement(
                        path=path,
                        mode="100644",
                        type="blob",
                        content=content
                    )
                )

            for path in (deletes or []):
                tree_elements.append(
                    InputGitTreeElement(
                        path=path,
                        mode="100644",
                        type="blob",
                        sha=None
                    )
                )

            new_tree = self.repo.create_git_tree(tree_elements, base_tree=base_tree)
            new_commit = self.repo.create_git_commit(commit_message, new_tree, [base_commit])
            ref.edit(new_commit.sha)
            logger.info(f"Committed {len(updates or {})} updates and {len(deletes or [])} deletes in single commit: {new_commit.sha}")
            return True
        except Exception as e:
            logger.warning(f"Git Tree atomic commit failed ({e}), falling back to sequential API calls...")
            # Fallback: 개별 API 호출
            for path, content in (updates or {}).items():
                self._commit_or_update_file(path, content, commit_message)
            for path in (deletes or []):
                try:
                    file_obj = self.repo.get_contents(path, ref=self.branch)
                    self.repo.delete_file(
                        path=path,
                        message=commit_message,
                        sha=file_obj.sha,
                        branch=self.branch
                    )
                except Exception as de:
                    logger.warning(f"Fallback delete failed for {path}: {de}")
            return True

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
            updates = {filepath: formatted_ko}
            uploaded_paths = [filepath]

            # 다국어 번역본 준비
            if translations:
                for lang, trans_content in translations.items():
                    lang_path = f"{lang}/{folder_name}/{filename}"
                    formatted_trans = self._format_markdown_for_lang(trans_content, lang=lang)
                    updates[lang_path] = formatted_trans
                    uploaded_paths.append(lang_path)

            # tech 포스트인 경우 index.md (KO/EN/JA) 업데이트 준비
            if folder_name == "tech":
                index_updates = self._prepare_tech_index_updates(filename, formatted_ko, translations=translations)
                if index_updates:
                    updates.update(index_updates)
                    uploaded_paths.append("tech/index.md (자동 색인)")

            commit_msg = f"docs: Auto post from Telegram Bot ({title})"
            self._commit_multiple_changes(updates=updates, deletes=[], commit_message=commit_msg)
            self.last_uploaded = (folder_name, filename)

            return True, f"GitHub ({', '.join(uploaded_paths)})"
        except Exception as e:
            return False, str(e)

    def save_image_and_push(self, file_bytes, filename):
        if not self.repo:
            return False, "GitHub repository connection not configured (check GITHUB_TOKEN)."

        filepath = f"public/images/{filename}"
        commit_msg = f"docs: Upload image {filename}"

        try:
            self._commit_or_update_file(filepath, bytes(file_bytes), commit_msg)
            return True, f"![이미지](/images/{filename})"
        except Exception as e:
            return False, str(e)

    def save_and_push_draft(self, markdown_content, title=None, folder_name="blog"):
        """draft: true 속성을 주입하여 초안으로 저장 및 푸시 (검색엔진/목록 숨김)"""
        if not re.search(r'^draft:\s*', markdown_content, re.MULTILINE):
            if markdown_content.startswith('---'):
                parts = markdown_content.split('---', 2)
                if len(parts) >= 3:
                    markdown_content = f"---{parts[1]}\ndraft: true\n---{parts[2]}"
            else:
                markdown_content = f"---\ndraft: true\n---\n\n{markdown_content}"
        return self.save_and_push(markdown_content, title=title, folder_name=folder_name, translations=None)

    def list_all_posts(self, recent_hints: list = None) -> list:
        """
        저장소의 tech/ 및 blog/ 폴더에 있는 모든 게시글 목록을 (folder, filename) 튜플 리스트로 반환합니다.
        recent_hints(최근 작성 이력)가 있으면 해당 글을 최상단에 배치합니다.
        """
        if not self.repo:
            return []
        all_posts = []
        for folder in ["tech", "blog"]:
            try:
                contents = self.repo.get_contents(folder, ref=self.branch)
                folder_items = [
                    (folder, item.name)
                    for item in contents
                    if item.name.endswith(".md") and item.name != "index.md"
                ]
                folder_items.sort(key=lambda x: x[1], reverse=True)
                all_posts.extend(folder_items)
            except Exception as e:
                logger.warning(f"Error listing {folder}: {e}")

        if recent_hints:
            existing_set = set(all_posts)
            prioritized = []
            for h in recent_hints:
                pair = (h.get("folder"), h.get("filename"))
                if pair in existing_set and pair not in prioritized:
                    prioritized.append(pair)
            remainder = [p for p in all_posts if p not in prioritized]
            return prioritized + remainder

        return all_posts

    def find_post_files(self, keyword):
        """키워드나 파일명으로 블로그 포스트 파일 검색"""
        if not self.repo:
            return []
        found = []
        kw = keyword.strip().lower()
        for folder in ["tech", "blog"]:
            try:
                contents = self.repo.get_contents(folder, ref=self.branch)
                for item in contents:
                    if item.name.endswith(".md") and item.name != "index.md":
                        if not kw or kw in item.name.lower():
                            found.append((folder, item.name, item.sha))
            except Exception:
                pass
        return found

    def unpublish_post_by_target(self, folder: str, filename: str):
        """특정 (folder, filename) 게시글의 frontmatter에 draft: true를 추가하고 tech/index.md에서 제외 (단일 커밋)"""
        if not self.repo:
            return False, "GitHub 저장소 미연결"

        updates = {}
        updated_paths = []

        for prefix in ["", "ja/", "en/"]:
            path = f"{prefix}{folder}/{filename}"
            try:
                file_obj = self.repo.get_contents(path, ref=self.branch)
                content = file_obj.decoded_content.decode("utf-8")
                if not re.search(r'^draft:\s*true', content, re.MULTILINE | re.IGNORECASE):
                    if re.search(r'^draft:\s*false', content, re.MULTILINE | re.IGNORECASE):
                        new_content = re.sub(r'^draft:\s*false', 'draft: true', content, flags=re.MULTILINE | re.IGNORECASE)
                    elif content.startswith('---'):
                        parts = content.split('---', 2)
                        if len(parts) >= 3:
                            new_content = f"---{parts[1]}\ndraft: true\n---{parts[2]}"
                        else:
                            new_content = f"---\ndraft: true\n---\n\n{content}"
                    else:
                        new_content = f"---\ndraft: true\n---\n\n{content}"

                    updates[path] = new_content
                    updated_paths.append(path)
            except Exception as e:
                logger.warning(f"Failed to read {path} for unpublish: {e}")

        if folder == "tech":
            index_removals = self._prepare_tech_index_removals(filename)
            if index_removals:
                updates.update(index_removals)
                updated_paths.extend(list(index_removals.keys()))

        if not updates:
            return False, f"이미 비공개 상태이거나 파일을 찾을 수 없습니다: [{folder}] {filename}"

        try:
            self._commit_multiple_changes(
                updates=updates,
                deletes=[],
                commit_message=f"chore: Unpublish post (draft: true) - {filename}"
            )
            return True, f"비공개(초안) 전환 완료 (단일 커밋):\n" + "\n".join(updated_paths)
        except Exception as e:
            return False, f"비공개 전환 중 오류 발생: {e}"

    def delete_post_by_target(self, folder: str, filename: str, delete_images: bool = True):
        """
        특정 (folder, filename) 게시글을 KO, EN, JA 3개 언어에서 영구 삭제하고,
        tech/index.md 표 행 및 본문 내 첨부 이미지까지 단일 커밋으로 정리합니다.
        """
        if not self.repo:
            return False, "GitHub 저장소 미연결"

        deletes = []
        updates = {}
        ko_content = ""

        for prefix in ["", "ja/", "en/"]:
            path = f"{prefix}{folder}/{filename}"
            try:
                file_obj = self.repo.get_contents(path, ref=self.branch)
                deletes.append(path)
                if prefix == "":
                    try:
                        ko_content = file_obj.decoded_content.decode("utf-8")
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"File not found for deletion ({path}): {e}")

        if not deletes:
            return False, f"삭제할 파일을 찾을 수 없습니다: [{folder}] {filename}"

        # 본문 내 첨부 이미지(public/images/img_...) 확인 및 삭제 목록 추가
        if delete_images and ko_content:
            img_paths = self._extract_image_paths(ko_content)
            for img_path in img_paths:
                try:
                    self.repo.get_contents(img_path, ref=self.branch)
                    deletes.append(img_path)
                except Exception:
                    pass

        # tech 포스트인 경우 3개 국어 tech/index.md에서 테이블 행 제거
        if folder == "tech":
            index_removals = self._prepare_tech_index_removals(filename)
            if index_removals:
                updates.update(index_removals)

        try:
            self._commit_multiple_changes(
                updates=updates,
                deletes=deletes,
                commit_message=f"chore: Delete post - {filename}"
            )
            summary_lines = [f"🗑️ 삭제된 파일: {p}" for p in deletes]
            if updates:
                summary_lines.extend([f"📝 색인 정리: {p}" for p in updates.keys()])
            return True, "영구 삭제 완료 (단일 커밋):\n" + "\n".join(summary_lines)
        except Exception as e:
            return False, f"삭제 처리 중 오류 발생: {e}"

    def unpublish_post(self, keyword):
        """키워드로 게시글을 찾아 비공개(draft: true)로 전환"""
        if not self.repo:
            return False, "GitHub 저장소 미연결"

        matches = self.find_post_files(keyword)
        if not matches:
            return False, f"'{keyword}'에 해당하는 게시글을 찾을 수 없습니다."
        if len(matches) > 1:
            names = [f"[{f}] {n}" for f, n, _ in matches[:5]]
            return False, f"일치하는 글이 여러 개 있습니다. 더 정확한 제목을 입력해 주세요:\n" + "\n".join(names)

        folder, filename, _ = matches[0]
        return self.unpublish_post_by_target(folder, filename)

    def delete_post(self, keyword):
        """키워드로 게시글을 찾아 KO, EN, JA 3개 언어 및 색인에서 영구 삭제"""
        if not self.repo:
            return False, "GitHub 저장소 미연결"

        matches = self.find_post_files(keyword)
        if not matches:
            return False, f"'{keyword}'에 해당하는 게시글을 찾을 수 없습니다."
        if len(matches) > 1:
            names = [f"[{f}] {n}" for f, n, _ in matches[:5]]
            return False, f"일치하는 글이 여러 개 있습니다. 더 정확한 제목을 입력해 주세요:\n" + "\n".join(names)

        folder, filename, _ = matches[0]
        return self.delete_post_by_target(folder, filename)
