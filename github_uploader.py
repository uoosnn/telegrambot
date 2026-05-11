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

    def save_and_push(self, markdown_content, title=None, folder_name="blog"):
        # Determine target folder
        target_dir = os.path.join(self.repo_path, folder_name)
        os.makedirs(target_dir, exist_ok=True)
        
        # Extract title from markdown if not provided
        if not title:
            title_match = re.search(r'title:\s*(.*)', markdown_content)
            title = title_match.group(1).strip() if title_match else "새로운_포스트"
        
        filename = self.generate_filename(title)
        filepath = os.path.join(target_dir, filename)
        
        # Remove markdown codeblock wrappers if AI added them
        if markdown_content.startswith("```markdown"):
            markdown_content = markdown_content[11:]
        if markdown_content.startswith("```"):
            markdown_content = markdown_content[3:]
        if markdown_content.endswith("```"):
            markdown_content = markdown_content[:-3]
            
        markdown_content = markdown_content.strip()
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(markdown_content)
            
        # Git commit and push
        try:
            self.repo.git.add(filepath)
            self.repo.index.commit(f"docs: Auto post from Telegram Bot ({title})")
            origin = self.repo.remote(name='origin')
            origin.push()
            return True, filepath
        except Exception as e:
            print(f"Git Push Error: {e}")
            return False, str(e)
