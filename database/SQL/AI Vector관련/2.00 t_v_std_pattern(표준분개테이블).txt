CREATE TABLE t_v_std_pattern (
    id          SERIAL PRIMARY KEY,
    pattern_nm  VARCHAR(50),          -- 패턴명 (매출증가, 매입감소 등)
    example_tx  TEXT,                 -- 샘플 예제 문장
    embedding   VECTOR(1024),         -- 벡터 데이터 (OpenAI Sonnet 등 임베딩 크기)
    
    -- 매칭될 기본 분개 정보 (JSONB로 저장하여 유연성 확보)
    docty       CHAR(2),              -- CI, SI, GL 등
    journal_json JSONB,               -- 제안할 차/대변 계정 정보
    
    reg_date    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 검색 속도를 높이기 위한 인덱스 (IVFFlat 또는 HNSW)
CREATE INDEX ON t_v_std_pattern USING hnsw (embedding vector_cosine_ops);