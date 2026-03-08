-- [거래] 부가세 상세 내역 테이블
CREATE TABLE t_ctax (
    -- 기본키 (PK) 구성
    comcd       VARCHAR(10)   NOT NULL, -- 회사코드
    docno       VARCHAR(10)   NOT NULL, -- 전표번호
    fisyr       NUMERIC(4)    NOT NULL, -- 회계연도 (회계기간)
    lineno      NUMERIC(4)    NOT NULL, -- Tax 전표 라인 아이템 번호 (예: 0001)
    
    -- 세무 관리 정보
    taxcd       CHAR(2)       NOT NULL, -- 세금 코드 (Tax Code)
    debcre      CHAR(1)       NOT NULL, -- 차대구분 지시자 (D:차변, C:대변)
    taxtyp      CHAR(2),                -- Tax 유형 (AP:매입, AR:매출)
    
    -- 원시 전표 추적 정보 (Traceability)
    srcdoc      VARCHAR(10),            -- 원시전표번호 (Source Document)
    srcyr       NUMERIC(4),             -- 원시전표 회계연도
    srclin      NUMERIC(4),             -- 원시전표 라인 번호
    
    -- 세무 금액 정보 (소수점 2자리 포함)
    bizamt      NUMERIC(25, 2) DEFAULT 0, -- 거래통화금액 (공급가액)
    locamt      NUMERIC(25, 2) DEFAULT 0, -- 로컬통화금액 (공급가액 환산)
    biztax      NUMERIC(25, 2) DEFAULT 0, -- 거래통화 세액 (VAT 금액)
    loctax      NUMERIC(25, 2) DEFAULT 0, -- 로컬통화 세액 (VAT 금액 환산)

    reg_date    TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

    -- 복합 기본키 설정
    CONSTRAINT pk_t_ctax PRIMARY KEY (comcd, docno, fisyr, lineno),
    
    -- 원장 헤더와의 관계 설정
    CONSTRAINT fk_ctax_header FOREIGN KEY (comcd, docno, fisyr) 
        REFERENCES t_lhead (comcd, docno, fisyr) ON DELETE CASCADE
);

COMMENT ON TABLE t_ctax IS '부가세 상세 내역 보조부 (VAT Detail Table)';