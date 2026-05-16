import os
import glob
from ai_processor import AIProcessor
from dotenv import load_dotenv

load_dotenv()

def main():
    # 번역할 디렉토리 목록 설정
    target_configs = [
        {
            "src": "/source/uoosnn.github.io/blog",
            "en": "/source/uoosnn.github.io/en/blog",
            "ja": "/source/uoosnn.github.io/ja/blog"
        },
        {
            "src": "/source/uoosnn.github.io/tech",
            "en": "/source/uoosnn.github.io/en/tech",
            "ja": "/source/uoosnn.github.io/ja/tech"
        }
    ]

    processor = AIProcessor()

    for config in target_configs:
        src_dir = config["src"]
        en_dir = config["en"]
        ja_dir = config["ja"]

        print(f"\n--- Checking directory: {src_dir} ---")
        
        os.makedirs(en_dir, exist_ok=True)
        os.makedirs(ja_dir, exist_ok=True)

        md_files = glob.glob(os.path.join(src_dir, "*.md"))
        for file_path in md_files:
            filename = os.path.basename(file_path)
            if filename == "index.md":
                continue
                
            print(f"Checking {filename}...")
            
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
                
            # English translation
            en_path = os.path.join(en_dir, filename)
            if not os.path.exists(en_path):
                print(f"  -> Translating {filename} to English")
                en_content = processor.translate_blog_post(content, "English")
                with open(en_path, "w", encoding="utf-8") as f:
                    f.write(en_content)
            else:
                print(f"  -> English version already exists, skipping.")
                
            # Japanese translation
            ja_path = os.path.join(ja_dir, filename)
            if not os.path.exists(ja_path):
                print(f"  -> Translating {filename} to Japanese")
                ja_content = processor.translate_blog_post(content, "Japanese")
                with open(ja_path, "w", encoding="utf-8") as f:
                    f.write(ja_content)
            else:
                print(f"  -> Japanese version already exists, skipping.")

    print("\nAll translations complete!")

if __name__ == "__main__":
    main()
