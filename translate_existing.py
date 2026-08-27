import os
import glob
import time
import argparse
from ai_processor import AIProcessor
from dotenv import load_dotenv

load_dotenv()

def main():
    parser = argparse.ArgumentParser(description="Zero-Slop Multilingual Translator (EN / JA)")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing translated files")
    parser.add_argument("--file", type=str, help="Translate a specific file only")
    args = parser.parse_args()

    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "uoosnn.github.io"))

    target_configs = [
        {
            "src": os.path.join(base_dir, "blog"),
            "en": os.path.join(base_dir, "en", "blog"),
            "ja": os.path.join(base_dir, "ja", "blog")
        },
        {
            "src": os.path.join(base_dir, "tech"),
            "en": os.path.join(base_dir, "en", "tech"),
            "ja": os.path.join(base_dir, "ja", "tech")
        }
    ]

    processor = AIProcessor()

    for config in target_configs:
        src_dir = config["src"]
        en_dir = config["en"]
        ja_dir = config["ja"]

        print(f"\n=== Processing directory: {src_dir} ===")
        os.makedirs(en_dir, exist_ok=True)
        os.makedirs(ja_dir, exist_ok=True)

        if args.file:
            md_files = [os.path.abspath(args.file)] if os.path.dirname(os.path.abspath(args.file)) == src_dir else []
        else:
            md_files = glob.glob(os.path.join(src_dir, "*.md"))

        for file_path in md_files:
            filename = os.path.basename(file_path)
            if filename == "index.md":
                continue

            print(f"\nChecking {filename}...")

            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            # English translation
            en_path = os.path.join(en_dir, filename)
            if args.overwrite or not os.path.exists(en_path):
                print(f"  -> Translating to English (gemini-2.5-flash)...")
                try:
                    en_content = processor.translate_blog_post(content, "English")
                    with open(en_path, "w", encoding="utf-8") as f:
                        f.write(en_content)
                    print(f"  -> Saved English version.")
                    time.sleep(1)
                except Exception as e:
                    print(f"  -> ERROR translating to English: {e}")
            else:
                print(f"  -> English version exists, skipping.")

            # Japanese translation
            ja_path = os.path.join(ja_dir, filename)
            if args.overwrite or not os.path.exists(ja_path):
                print(f"  -> Translating to Japanese (gemini-2.5-flash)...")
                try:
                    ja_content = processor.translate_blog_post(content, "Japanese")
                    with open(ja_path, "w", encoding="utf-8") as f:
                        f.write(ja_content)
                    print(f"  -> Saved Japanese version.")
                    time.sleep(1)
                except Exception as e:
                    print(f"  -> ERROR translating to Japanese: {e}")
            else:
                print(f"  -> Japanese version exists, skipping.")

    print("\n=== All translations complete! ===")

if __name__ == "__main__":
    main()
