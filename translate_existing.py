import os
import glob
from ai_processor import AIProcessor
from dotenv import load_dotenv

load_dotenv()

def main():
    blog_dir = "/source/uoosnn.github.io/blog"
    en_dir = "/source/uoosnn.github.io/en/blog"
    ja_dir = "/source/uoosnn.github.io/ja/blog"

    os.makedirs(en_dir, exist_ok=True)
    os.makedirs(ja_dir, exist_ok=True)

    processor = AIProcessor()

    md_files = glob.glob(os.path.join(blog_dir, "*.md"))
    for file_path in md_files:
        filename = os.path.basename(file_path)
        if filename == "index.md":
            continue
            
        print(f"Translating {filename}...")
        
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        # English translation
        en_path = os.path.join(en_dir, filename)
        if not os.path.exists(en_path):
            print("  -> Translating to English")
            en_content = processor.translate_blog_post(content, "English")
            with open(en_path, "w", encoding="utf-8") as f:
                f.write(en_content)
        else:
            print("  -> English version already exists, skipping.")
            
        # Japanese translation
        ja_path = os.path.join(ja_dir, filename)
        if not os.path.exists(ja_path):
            print("  -> Translating to Japanese")
            ja_content = processor.translate_blog_post(content, "Japanese")
            with open(ja_path, "w", encoding="utf-8") as f:
                f.write(ja_content)
        else:
            print("  -> Japanese version already exists, skipping.")

    print("Translation complete!")

if __name__ == "__main__":
    main()
