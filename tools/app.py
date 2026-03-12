import os
import voyageai
from flask import Flask, request, jsonify, send_file
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from dotenv import load_dotenv

# Dabom_RR 루트의 .env 파일을 읽어옵니다.
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

app = Flask(__name__)

# Voyage AI 설정
VOYAGE_KEY = "pa-N7bI6gFXwWTSZz3u_wKJKNEIwFlsak6N13VSyrUa_Hi"
vo = voyageai.Client(api_key=VOYAGE_KEY)

# DB 연결 설정
db_params = {
    "host": os.getenv("DB_HOST", "localhost"),
    "database": os.getenv("DB_NAME", "postgres"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASS", "230509") 
}

def get_embedding(text):
    """Voyage AI를 통해 진짜 의미 벡터를 생성합니다."""
    result = vo.embed([text], model="voyage-3", input_type="document")
    return result.embeddings[0]

@app.route('/')
def index():
    return send_file('index.html')

# --- 404 에러를 해결하는 핵심 API 경로들 ---

@app.route('/api/patterns', methods=['GET'])
def get_patterns():
    try:
        conn = psycopg2.connect(**db_params)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT id, pattern_nm, example_tx, docty, journal_json FROM t_v_std_pattern ORDER BY id DESC")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify(rows)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/pattern', methods=['POST'])
def create_pattern():
    data = request.json
    try:
        conn = psycopg2.connect(**db_params)
        cur = conn.cursor()
        # 진짜 Voyage AI 임베딩 생성
        embedding = get_embedding(data['example_tx'])
        
        sql = """INSERT INTO t_v_std_pattern (pattern_nm, example_tx, embedding, docty, journal_json)
                 VALUES (%s, %s, %s, %s, %s)"""
        cur.execute(sql, (data['pattern_nm'], data['example_tx'], embedding, data['docty'], Json(data['journal_json'])))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"status": "success", "message": "Voyage AI 지식이 성공적으로 저장되었습니다."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/pattern/<int:id>', methods=['PUT'])
def update_pattern(id):
    """
    패턴 수정 — ID를 유지하는 UPDATE 방식.
    [참조 분석 결과] t_v_std_pattern.id는 어떤 테이블에도 FK로 저장되지 않지만,
    mainai.py의 pattern_id 논리 참조 및 향후 확장 안전성을 위해 UPDATE를 채택.
    - example_tx 변경 시: Voyage AI 임베딩 재발급 후 embedding 컬럼도 업데이트
    - example_tx 미변경 시: 기존 임베딩 유지 (불필요한 API 호출 절감)
    """
    data = request.json
    conn = None
    try:
        conn = psycopg2.connect(**db_params)
        cur = conn.cursor()

        # 기존 example_tx 조회 → 변경 여부로 벡터 재발급 필요성 판단
        cur.execute("SELECT example_tx FROM t_v_std_pattern WHERE id=%s", (id,))
        old = cur.fetchone()
        if not old:
            return jsonify({"status": "error", "message": f"ID {id} 패턴을 찾을 수 없습니다."}), 404

        new_tx    = data['example_tx'].strip()
        tx_changed = (new_tx != (old[0] or '').strip())

        with conn:  # 트랜잭션: 예외 발생 시 자동 rollback
            if tx_changed:
                # 예시 문장 변경 → 벡터 재발급
                embedding = get_embedding(new_tx)
                cur.execute(
                    """UPDATE t_v_std_pattern
                       SET pattern_nm=%s, example_tx=%s, embedding=%s, docty=%s, journal_json=%s
                       WHERE id=%s""",
                    (data['pattern_nm'], new_tx, embedding, data['docty'],
                     Json(data['journal_json']), id)
                )
                msg = f"패턴 ID {id} 수정 완료 ✓ (벡터 재발급)"
            else:
                # 예시 문장 미변경 → 임베딩 그대로 유지
                cur.execute(
                    """UPDATE t_v_std_pattern
                       SET pattern_nm=%s, docty=%s, journal_json=%s
                       WHERE id=%s""",
                    (data['pattern_nm'], data['docty'], Json(data['journal_json']), id)
                )
                msg = f"패턴 ID {id} 수정 완료 ✓ (벡터 유지)"

        cur.close()
        return jsonify({"status": "success", "message": msg})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn:
            try: conn.close()
            except: pass

@app.route('/api/pattern/<int:id>', methods=['DELETE'])
def delete_pattern(id):
    conn = None
    try:
        conn = psycopg2.connect(**db_params)
        cur = conn.cursor()
        with conn:
            cur.execute("DELETE FROM t_v_std_pattern WHERE id=%s", (id,))
        cur.close()
        return jsonify({"status": "success", "message": "삭제 완료"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if conn:
            try: conn.close()
            except: pass

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)