import os
import re
from datetime import datetime
from git import Repo

class GithubUploader:
    def __init__(self):
        self.repo_path = os.getenv("TARGET_REPO_PATH", "/source/uoosnn.github.io")
        self.repo = Repo(self.repo_path)
    
    def generate_filename(self, title):
        # Remove invalid characters for filename
        clean_title = re.sub(r'[\\/*?:"<>|]', "", title)
        # Ensure it's not too long
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

    def save_and_push(self, markdown_content, title=None, folder_name="blog", translations=None):
        # Determine target folder
        target_dir = os.path.join(self.repo_path, folder_name)
        os.makedirs(target_dir, exist_ok=True)
        
        # Extract title from markdown if not provided
        if not title:
            title_match = re.search(r'title:\s*(.*)', markdown_content)
            title = title_match.group(1).strip() if title_match else "새로운_포스트"
            # Remove quotes if AI added them so filename is clean
            if title.startswith('"') and title.endswith('"'):
                title = title[1:-1]
        
        filename = self.generate_filename(title)
        filepath = os.path.join(target_dir, filename)
        
        markdown_content = self._clean_markdown(markdown_content)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(markdown_content)
            
        added_files = [filepath]

        # Save translations if provided
        if translations:
            for lang, trans_content in translations.items():
                lang_dir = os.path.join(self.repo_path, lang, folder_name)
                os.makedirs(lang_dir, exist_ok=True)
                lang_filepath = os.path.join(lang_dir, filename)
                
                trans_content = self._clean_markdown(trans_content)
                with open(lang_filepath, 'w', encoding='utf-8') as f:
                    f.write(trans_content)
                added_files.append(lang_filepath)
            
        # Git commit and push
        try:
            for file in added_files:
                self.repo.git.add(file)
            self.repo.index.commit(f"docs: Auto post from Telegram Bot ({title})")
            origin = self.repo.remote(name='origin')
            origin.push()
            return True, filepath
        except Exception as e:
            print(f"Git Push Error: {e}")
            return False, str(e)
