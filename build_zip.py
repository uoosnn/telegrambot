import os
import shutil
import subprocess
import zipfile
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BUILD_DIR = os.path.join(BASE_DIR, "build_package")
ZIP_OUTPUT = os.path.join(BASE_DIR, "lambda_deployment.zip")
REQ_FILE = os.path.join(BASE_DIR, "requirements.txt")

SOURCE_FILES = [
    "lambda_function.py",
    "ai_processor.py",
    "github_uploader.py",
    "dynamo_manager.py",
    "news_scraper.py"
]

def clean_build():
    if os.path.exists(BUILD_DIR):
        print(f"[Clean] Removing old build directory: {BUILD_DIR}")
        shutil.rmtree(BUILD_DIR)
    if os.path.exists(ZIP_OUTPUT):
        print(f"[Clean] Removing old zip: {ZIP_OUTPUT}")
        os.remove(ZIP_OUTPUT)

def install_dependencies():
    print("[1/3] Installing Linux (manylinux2014_x86_64) dependencies for AWS Lambda...")
    os.makedirs(BUILD_DIR, exist_ok=True)
    cmd = [
        sys.executable, "-m", "pip", "install",
        "--platform", "manylinux2014_x86_64",
        "--target", BUILD_DIR,
        "--implementation", "cp",
        "--python-version", "3.12",
        "--only-binary=:all:",
        "-r", REQ_FILE,
        "--upgrade"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[Error] pip install failed:\n{result.stderr}")
        sys.exit(1)
    print("[1/3] Linux dependencies installed successfully.")


def copy_source_files():
    print("[2/3] Copying source files...")
    for filename in SOURCE_FILES:
        src = os.path.join(BASE_DIR, filename)
        dst = os.path.join(BUILD_DIR, filename)
        if os.path.exists(src):
            shutil.copy2(src, dst)
            print(f"  + {filename}")
        else:
            print(f"  [Warning] {filename} not found!")

EXCLUDE_PATTERNS = [
    "boto3",
    "botocore",
    "s3transfer",
    "__pycache__",
    "*.dist-info",
    "*.egg-info",
    "*.pyc",
    "tests",
    "test"
]

def should_exclude(rel_path):
    parts = rel_path.replace("\\", "/").split("/")
    for part in parts:
        if part in ["__pycache__", "tests", "test", "boto3", "botocore", "s3transfer"]:
            return True
        if part.endswith(".dist-info") or part.endswith(".egg-info") or part.endswith(".pyc"):
            return True
    return False

def create_zip():
    print(f"[3/3] Creating optimized zip: {ZIP_OUTPUT}...")
    with zipfile.ZipFile(ZIP_OUTPUT, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(BUILD_DIR):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, BUILD_DIR)
                if not should_exclude(arcname):
                    zipf.write(file_path, arcname)
    
    zip_size_mb = os.path.getsize(ZIP_OUTPUT) / (1024 * 1024)
    print(f"[Success] Build Complete! Optimized file size: {zip_size_mb:.2f} MB")
    print(f"[Ready] Upload file: {ZIP_OUTPUT}")


def main():
    print("=" * 50)
    print("Building AWS Lambda Deployment Package")
    print("=" * 50)
    clean_build()
    install_dependencies()
    copy_source_files()
    create_zip()
    print("=" * 50)


if __name__ == "__main__":
    main()
