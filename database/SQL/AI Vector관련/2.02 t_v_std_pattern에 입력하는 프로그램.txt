from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import voyageai
import psycopg2
import json
import uvicorn
import sys
import io

# 윈도우 한글 인코딩 문제를 방지하기 위한 설정
sys.stdout = io.TextIOWrapper(sys.stdout.detach(), encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.detach(), encoding='utf-8')

app = FastAPI()

# 1. 설정 정보 (image_7c66fc.png 결과 반영)
VOYAGE_API_KEY = "pa-N7bI6gFXwWTSZz3u_wKJKNEIwFlsak6N13VSyrUa_Hi"
DB_CONFIG = {
    "host": "localhost",
    "database": "postgres", # 확인된 DB 이름
    "user": "postgres",     # 확인된 사용자
    "password": "230509",   # 사용자님의 비밀번호
    "port": 5432            # 확인된 포트
}

vo = voyageai.Client(api_key=VOYAGE_API_KEY)

@app.post("/api/save")
async def save_data(request: Request):
    conn = None
    try:
        data = await request.json()
        print(f"📥 데이터 수신: {data['pattern_nm']}")

        # (1) Voyage AI 임베딩 생성
        result = vo.embed([data['example_tx']], model="voyage-finance-2", input_type="document")
        embedding = result.embeddings[0]

        # (2) DB 연결 및 저장
        conn = psycopg2.connect(**DB_CONFIG)
        conn.set_client_encoding('UTF8') # 연결 직후 인코딩 설정
        cur = conn.cursor()
        
        sql = """
            INSERT INTO t_v_std_pattern (pattern_nm, example_tx, embedding, docty, journal_json)
            VALUES (%s, %s, %s, %s, %s)
        """
        cur.execute(sql, (
            data['pattern_nm'], data['example_tx'], embedding, 
            data['docty'], json.dumps({"items": data['journal']}, ensure_ascii=False)
        ))
        
        conn.commit()
        cur.close()
        print("✅ DB 저장 완료!")
        return {"status": "success", "message": "성공적으로 저장되었습니다!"}

    except Exception as e:
        # 에러 메시지 인코딩 방어 로직
        try:
            # 윈도우 한글 에러(CP949)를 바이트로 변환 후 다시 읽기 시도
            error_msg = str(e).encode('iso-8859-1').decode('cp949')
        except:
            error_msg = str(e)
            
        print(f"❌ 서버 내부 오류 발생: {error_msg}")
        return {"status": "error", "message": f"서버 오류: {error_msg}"}
    finally:
        if conn:
            conn.close()

@app.get("/", response_class=HTMLResponse)
async def main_page():
    with open("std_loader.html", "r", encoding="utf-8") as f:
        return f.read()

if __name__ == "__main__":
    print("🚀 다봄 지식 로더 서버를 시작합니다 (http://localhost:8000)")
    uvicorn.run(app, host="0.0.0.0", port=8000)