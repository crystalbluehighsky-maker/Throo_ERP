-- 기존 테이블 삭제 (초기화용)
DROP TABLE IF EXISTS t_nbank CASCADE;

CREATE TABLE t_nbank (
    bctry       CHAR(2)       NOT NULL, -- 은행국가 (예: KR, US)
    bankcd      VARCHAR(20)   NOT NULL, -- 은행키/코드 (예: 082)
    bnknm       VARCHAR(50)   NOT NULL, -- 은행명 (예: 신한은행)
    swift       VARCHAR(20),            -- 스위프트코드 (국제 송금용)
    address     VARCHAR(150),           -- 은행 주소 (필드명 '주소' 반영)
    
    -- 시스템 관리용 필드 (추천 추가)
    use_yn      CHAR(1)       DEFAULT 'Y', 
    reg_date    TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

    -- PK 설정: 국가와 은행코드의 조합
    CONSTRAINT pk_t_nbank PRIMARY KEY (bctry, bankcd)
);

-- 테이블 및 컬럼 설명
COMMENT ON TABLE t_nbank IS '다봄 전역 은행코드 마스터 테이블';
COMMENT ON COLUMN t_nbank.bctry IS 'ISO 국가코드 2자리';
COMMENT ON COLUMN t_nbank.bankcd IS '은행 식별 코드';