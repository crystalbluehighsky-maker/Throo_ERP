import os
from dotenv import load_dotenv, find_dotenv

current_dir = os.path.dirname(os.path.abspath(__file__))

print("=== 1. 파이썬이 보는 실제 파일 이름들 ===")
print(os.listdir(current_dir))
print("=====================================\n")

print("=== 2. .env 자동 탐색 ===")
env_path = find_dotenv()
print(f"찾은 경로: '{env_path}'")

if env_path:
    load_dotenv(env_path)
    print("Google Key:", os.environ.get("GOOGLE_API_KEY"))
    print("Anthropic Key:", os.environ.get("ANTHROPIC_API_KEY"))
else:
    print(".env 파일을 찾을 수 없습니다!")