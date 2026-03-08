-- [마스터] 고객사별 회계기간(마감) 관리 테이블
CREATE TABLE t_aclose (
    comcd       CHAR(10)      NOT NULL, -- 회사코드 (PK)
    gubun       CHAR(2)       NOT NULL, -- 구분 (PK, 예: GL-일반전표, AR-매출 등)
    frprd       VARCHAR(8)    NOT NULL, -- 시작년월 (예: 20250101)
    enprd       VARCHAR(8)    NOT NULL, -- 끝년월 (예: 20251231)
    
    -- 향후 사용 예정 필드
    actprd      VARCHAR(8),             -- 회계팀 전용 시작년월
    actend      VARCHAR(8),             -- 회계팀 전용 끝년월

    reg_date    TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

    -- PK 설정: 회사 + 구분 조합
    CONSTRAINT pk_t_aclose PRIMARY KEY (comcd, gubun),
    
    -- 회사 마스터 참조
    CONSTRAINT fk_aclose_company FOREIGN KEY (comcd) 
        REFERENCES t_company (comcd) ON DELETE CASCADE
);

COMMENT ON TABLE t_aclose IS 'DaBom 고객사별 회계기간 및 마감 제어 마스터';