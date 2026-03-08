DROP TABLE IF EXISTS t_v_user_learn;

CREATE TABLE t_v_user_learn (
    id          SERIAL PRIMARY KEY,
    comcd       VARCHAR(10),          -- 회사별 맞춤 학습
    usrnm       VARCHAR(10),          -- 사용자별 맞춤 학습
    input_tx    TEXT UNIQUE,          -- 💡 핵심: UNIQUE 제약조건 추가
    embedding   VECTOR(1024),         -- 입력 문장의 벡터
    final_json  JSONB,                -- 수정된 차/대변 데이터
    hit_count   INTEGER DEFAULT 1,    -- 동일 패턴 반복 횟수
    upd_date    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX ON t_v_user_learn USING hnsw (embedding vector_cosine_ops);