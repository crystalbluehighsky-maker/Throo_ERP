-- [표준] 다봄 표준 세금 코드 테이블 (Template)
CREATE TABLE t_ntxkey (
    taxcd       VARCHAR(10)   NOT NULL, -- 세금 코드 (PK, 예: V1, V0)
    taxnm       VARCHAR(50)   NOT NULL, -- 세금 코드명 (예: 매입과세 10%)
    taxtyp      CHAR(1)       NOT NULL, -- 세금 구분 (I: 매입/Input, O: 매출/Output)
    taxrate     NUMERIC(5, 2) DEFAULT 0, -- 세율 (예: 10.00)
    
    -- 전역 규칙: 통화키 적용
    currency    CHAR(3)       DEFAULT 'KRW',
    
    -- 표준 GL 계정 (t_nglmst 참조용 정보)
    glmaster    VARCHAR(6),
    
    reg_date    TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT pk_t_ntxkey PRIMARY KEY (taxcd)
);

COMMENT ON TABLE t_ntxkey IS '다봄 표준 세금 코드 마스터 (템플릿)';


-- [마스터] 회사별 세금 코드 테이블 (실제 사용)
CREATE TABLE t_ctxkey (
    comcd       VARCHAR(10)   NOT NULL, -- 회사코드 (PK)
    taxcd       VARCHAR(10)   NOT NULL, -- 세금 코드 (PK)
    
    taxnm       VARCHAR(50)   NOT NULL, -- 회사별 세금 명칭
    taxtyp      CHAR(1)       NOT NULL, -- 세금 구분
    taxrate     NUMERIC(5, 2) DEFAULT 0, -- 세율
    currency    CHAR(3)       DEFAULT 'KRW',
    
    -- 회사별 GL 계정 연동 (t_cglmst 참조)
    glmaster    VARCHAR(6)    ,
    
    use_yn      CHAR(1)       DEFAULT 'Y',
    reg_date    TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

    -- PK 설정: 회사 + 세금코드
    CONSTRAINT pk_t_ctxkey PRIMARY KEY (comcd, taxcd),
    
    -- FK 설정
    CONSTRAINT fk_ctxkey_company FOREIGN KEY (comcd) 
        REFERENCES t_company (comcd) ON DELETE CASCADE,
    CONSTRAINT fk_ctxkey_standard FOREIGN KEY (taxcd) 
        REFERENCES t_ntxkey (taxcd),
    CONSTRAINT fk_ctxkey_gl FOREIGN KEY (comcd, glmaster) 
        REFERENCES t_cglmst (comcd, glmaster)
);

COMMENT ON TABLE t_ctxkey IS '다봄 고객사별 세금 코드 마스터';