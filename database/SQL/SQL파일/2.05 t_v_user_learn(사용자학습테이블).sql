CREATE TABLE t_v_user_learn (
    id          SERIAL PRIMARY KEY,
    comcd       VARCHAR(10),          -- 회사별 맞춤 학습을 위해 필요
    usrnm       VARCHAR(10),          -- 사용자별 맞춤 학습
    input_tx    TEXT,                 -- 사용자가 실제 입력한 자연어
    embedding   VECTOR(1024),         -- 입력 문장의 벡터
    
    -- 사용자가 최종적으로 확정한 분개 결과
    final_json  JSONB,                -- 수정된 차/대변 데이터
    hit_count   INTEGER DEFAULT 1,    -- 동일 패턴 반복 횟수
    
    upd_date    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX ON t_v_user_learn USING hnsw (embedding vector_cosine_ops);