CREATE TABLE t_cbizpt (
    comcd       VARCHAR(10)   NOT NULL, -- 회사코드 (PK)
    bizptcd     VARCHAR(10)   NOT NULL, -- 거래처 코드 (PK)
    
    -- 기본 정보
    bizname1    VARCHAR(50)   NOT NULL, -- 거래처 이름 1
    bizname2    VARCHAR(50),            -- 거래처 이름 2
    bizadd1     VARCHAR(70)   NOT NULL, -- 거래처 주소 1
    bizadd2     VARCHAR(70),            -- 거래처 주소 2
    bizpostcd   VARCHAR(6),             -- 우편번호
    bizctry     VARCHAR(2)    DEFAULT 'KR', -- 국가코드 (예: KR)
    
    -- 연락처 정보
    bizphone1   VARCHAR(20),            -- 전화번호 1
    bizphone2   VARCHAR(20),            -- 전화번호 2 (중복 필드 수정 반영)
    bizemail1   VARCHAR(30),            -- 담당자 이메일 1
    bizemail2   VARCHAR(30),            -- 담당자 이메일 2
    bizfax      VARCHAR(30),            -- 팩스번호
    
    -- 사업자 정보
    bizceo      VARCHAR(20)   NOT NULL, -- 대표자명
    bizjong     VARCHAR(50)   NOT NULL, -- 업종
    biztae      VARCHAR(50)   NOT NULL, -- 업태
    biztax1     VARCHAR(13),            -- 주민번호 (개인사업자 등)
    biztax2     VARCHAR(10)   NOT NULL, -- 사업자번호
    biztax3     VARCHAR(10),            -- 법인번호
    
    -- 세금계산서 담당자 정보
    biztxname1  VARCHAR(20)   NOT NULL, -- 세무 담당자 이름 1
    biztxemail1 VARCHAR(30)   NOT NULL, -- 세무 담당자 이메일 1
    biztxname2  VARCHAR(20),            -- 세무 담당자 이름 2
    biztxemail2 VARCHAR(30),            -- 세무 담당자 이메일 2
    
    -- 회계 및 지급 조건 (공급업체/매입 관련)
    suppgl      VARCHAR(6),             -- 공급업체 연결 GL (t_cglmst 참조)
    suppterm    CHAR(4),                -- 지급조건 (예: 0001)
    suppmth     CHAR(1),                -- 지급방법 (예: T-송금)
    
    -- 회계 및 수금 조건 (고객/매출 관련)
    custgl      VARCHAR(6),             -- 고객 연결 GL (t_cglmst 참조)
    custterm    CHAR(4),                -- 수금조건
    custmth     CHAR(1),                -- 수금방법
    
    -- 시스템 관리
    use_yn      CHAR(1)       DEFAULT 'Y',
    reg_date    TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

    -- PK 설정
    CONSTRAINT pk_t_cbizpt PRIMARY KEY (comcd, bizptcd),
    
    -- FK 설정: 회사 및 계정 마스터와 연결
    CONSTRAINT fk_cbizpt_company FOREIGN KEY (comcd) 
        REFERENCES t_company (comcd) ON DELETE CASCADE
        
    -- 아래 FK는 t_cglmst가 완벽히 구축된 후 활성화하는 것을 권장합니다.
    -- CONSTRAINT fk_cbizpt_suppgl FOREIGN KEY (comcd, suppgl) REFERENCES t_cglmst (comcd, glmaster),
    -- CONSTRAINT fk_cbizpt_custgl FOREIGN KEY (comcd, custgl) REFERENCES t_cglmst (comcd, glmaster)
);

COMMENT ON TABLE t_cbizpt IS '다봄 고객사별 거래처(공급업체/고객) 통합 마스터';