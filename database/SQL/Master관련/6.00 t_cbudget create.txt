-- [수정 마스터] 통화키가 추가된 예산 관리 테이블
CREATE TABLE t_cbudget (
    comcd       VARCHAR(10)   NOT NULL, -- 회사코드 (PK)
    yymm        VARCHAR(6)    NOT NULL, -- 예산년월 (PK, 예: 202505)
    glmaster    VARCHAR(6)    NOT NULL, -- GL계정 코드 (PK)
    cctrcd      VARCHAR(10)   NOT NULL, -- 비용부서 코드 (PK)
    currency    CHAR(3)       NOT NULL DEFAULT 'KRW', -- 통화키 (PK 추가, 예: KRW, USD)
    
    bdgamt      NUMERIC(18, 2) DEFAULT 0, -- 예산금액
    reg_date    TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

    -- [핵심] 통화키를 포함한 5개 필드 복합 PK 설정
    CONSTRAINT pk_t_cbudget PRIMARY KEY (comcd, yymm, glmaster, cctrcd, currency),
    
    CONSTRAINT fk_cbudget_company FOREIGN KEY (comcd) 
        REFERENCES t_company (comcd) ON DELETE CASCADE,
    CONSTRAINT fk_cbudget_gl FOREIGN KEY (comcd, glmaster) 
        REFERENCES t_cglmst (comcd, glmaster)
);

COMMENT ON COLUMN t_cbudget.currency IS '예산 책정 통화 단위 (ISO 코드)';