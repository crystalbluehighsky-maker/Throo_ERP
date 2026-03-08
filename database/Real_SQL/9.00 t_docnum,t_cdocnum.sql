-- [표준] 다봄 표준 전표 번호 범위 테이블
CREATE TABLE t_ndocnum (
    doctype     CHAR(2)       NOT NULL, -- 전표 유형 (PK, 예: GL, IP, OP)
    doctext     VARCHAR(30)   NOT NULL, -- 전표 유형명 (예: 일반GL전표)
    frnum       NUMERIC(10)   NOT NULL, -- 전표번호 시작 (예: 1000000001)
    tonum       NUMERIC(10)   NOT NULL, -- 전표번호 끝 (예: 1199999999)
    
    reg_date    TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT pk_t_ndocnum PRIMARY KEY (doctype)
);

COMMENT ON TABLE t_ndocnum IS 'DaBom 표준 전표 번호 범위 마스터 (Template)';


-- [마스터] 고객사별 회계연도 기준 전표 채번 관리 테이블
CREATE TABLE t_cdocnum (
    comcd       VARCHAR(10)   NOT NULL, -- 회사코드 (PK)
    fisyr       NUMERIC(4, 0) NOT NULL, -- 회계연도 (PK, 예: 2025)
    doctype     CHAR(2)       NOT NULL, -- 전표 유형 (PK, 예: GL)
    
    doctext     VARCHAR(30),            -- 전표 유형명
    frnum       NUMERIC(10, 0) NOT NULL, -- 전표번호 시작 범위
    tonum       NUMERIC(10, 0) NOT NULL, -- 전표번호 종료 범위
    maxnum      NUMERIC(10, 0) NOT NULL, -- 현재(마지막) 발급 전표번호
    
    reg_date    TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

    -- PK 설정: 회사 + 연도 + 유형
    CONSTRAINT pk_t_cdocnum PRIMARY KEY (comcd, fisyr, doctype),
    
    -- 회사 마스터 참조 (무결성 보장)
    CONSTRAINT fk_cdocnum_company FOREIGN KEY (comcd) 
        REFERENCES t_company (comcd) ON DELETE CASCADE
);

COMMENT ON TABLE t_cdocnum IS 'DaBom 고객사별 연도별 전표 채번 마스터';