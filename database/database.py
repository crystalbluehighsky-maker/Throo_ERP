# database.py
import os
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

# 현재 파일(database.py)의 상위 폴더(Dabom_RR) 경로 계산
CURRENT_FILE_PATH = os.path.abspath(__file__)
BASE_DIR = os.path.dirname(os.path.dirname(CURRENT_FILE_PATH))
ENV_PATH = os.path.join(BASE_DIR, ".env")

# .env 로드 시도
if os.path.exists(ENV_PATH):
    load_dotenv(ENV_PATH)
    print(f"[INFO] Loaded .env from {ENV_PATH}")
else:
    print(f"[WARNING] .env file not found at {ENV_PATH}")

DATABASE_URL = os.getenv("DATABASE_URL")

# DATABASE_URL이 없으면 에러 발생 (원인 파악 용이)
if not DATABASE_URL:
    raise ValueError(f"DATABASE_URL is not set. Checked .env at: {ENV_PATH}")

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()