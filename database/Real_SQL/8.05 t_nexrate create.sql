-- [표준] 다봄 표준 환율 관리 테이블 (Narin Standard)
CREATE TABLE t_nexrate (
    extype      CHAR(1)       NOT NULL, -- 환율타입 (PK, 예: S-표준환율)
    srccur      CHAR(3)       NOT NULL, -- 소스통화 (PK, 예: USD) [전역규칙 준수]
    tarcur      CHAR(3)       NOT NULL, -- 타켓통화 (PK, 예: KRW) [전역규칙 준수]
    date        DATE          NOT NULL, -- 환율시작일 (PK)
    
    exrat       NUMERIC(9, 5) NOT NULL, -- 환율 (예: 1400.35000)
    
    reg_date    TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

    -- 환율 이력 관리를 위해 날짜를 포함한 복합 PK 설정
    CONSTRAINT pk_t_nexrate PRIMARY KEY (extype, srccur, tarcur, date)
);

COMMENT ON TABLE t_nexrate IS '다봄 표준 환율 마스터 (날짜별 이력 관리)';