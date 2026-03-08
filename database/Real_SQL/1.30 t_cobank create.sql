-- 기존 테이블 삭제
DROP TABLE IF EXISTS t_cobank CASCADE;

CREATE TABLE t_cobank (
    -- 기본 식별 정보 (PK 조합)
    comcd       VARCHAR(10)   NOT NULL, -- 회사코드
    bctry       CHAR(2)       NOT NULL, -- 은행국가 (KR 등)
    bankcd      VARCHAR(20)   NOT NULL, -- 은행코드 (088 등)
    branch      VARCHAR(20)   NOT NULL, -- 은행지점코드 (사용자 부여값: 088201 등)
    bookid      VARCHAR(20)   NOT NULL, -- 통장키 (사용자 부여값: shys1 등)
    
    -- 상세 정보
    bnknm       VARCHAR(50),            -- 은행명 (t_nbank 참조용 혹은 기록용)
    brchnm      VARCHAR(50)   NOT NULL, -- 우리통장이름/지점명 (신한은행-역삼지점 등)
    booknm      VARCHAR(50)   NOT NULL, -- 통장명 (보통예금 등)
    acctno      VARCHAR(30)   NOT NULL, -- 실제 계좌번호
    curren      VARCHAR(5)    DEFAULT 'KRW', -- 통화 (KRW, USD 등)
    gl          VARCHAR(10)   NOT NULL, -- 연결 회계 계정 코드
    
    -- 관리 필드
    moacct      CHAR(1)       DEFAULT 'N', -- 모계좌여부 (Y/N)
    text        VARCHAR(100),              -- 메모
    use_yn      CHAR(1)       DEFAULT 'Y', 
    reg_date    TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

    -- [핵심] 5개 필드 복합 PK 설정
    CONSTRAINT pk_t_cobank PRIMARY KEY (comcd, bctry, bankcd, branch, bookid),
    
    -- 외래키 설정
    CONSTRAINT fk_cobank_company FOREIGN KEY (comcd) 
        REFERENCES t_company (comcd) ON DELETE CASCADE,
    CONSTRAINT fk_cobank_nbank FOREIGN KEY (bctry, bankcd) 
        REFERENCES t_nbank (bctry, bankcd)
);

-- 인덱스 추가 (조회 성능 최적화)
CREATE INDEX idx_t_cobank_acct ON t_cobank(comcd, acctno);

COMMENT ON TABLE t_cobank IS '고객사별 지점 및 통장 단위 상세 계좌 마스터';