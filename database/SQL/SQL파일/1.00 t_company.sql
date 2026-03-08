-- 기존 테이블이 있다면 삭제 (초기화용)
DROP TABLE IF EXISTS t_company CASCADE;

CREATE TABLE t_company (
    -- 기본 정보
    comcd       VARCHAR(10)   NOT NULL, -- 회사코드 (Primary Key)
    cname       VARCHAR(100)  NOT NULL, -- 회사이름 (Not Null)
    citycd      VARCHAR(30)   NOT NULL, -- 도시 (Not Null)
    country     VARCHAR(6),             -- 나라 (KR 등)
    chartcd     VARCHAR(7),             -- 계정과목표 (NRCHART 등)
    postcd      VARCHAR(6),             -- 우편번호
    address     VARCHAR(300)  NOT NULL, -- 주소 (Not Null)
    
    -- 세무 정보
    taxcd1      VARCHAR(20),            -- 주민번호
    taxcd2      VARCHAR(10)   NOT NULL, -- 사업자번호 (Not Null)
    taxcd3      VARCHAR(30),            -- 법인번호
    taxcd4      VARCHAR(30),            -- 추가 Tax 번호4
    
    -- 대표 및 업종 정보
    ceo         VARCHAR(20),            -- 회사 대표자명
    upjong      VARCHAR(50),            -- 업종
    uptae       VARCHAR(50),            -- 업태
    
    -- 담당자 정보
    email1      VARCHAR(30)   NOT NULL, -- 주담당자 이메일 (Not Null)
    emial2      VARCHAR(30),            -- 부담당자 이메일
    empnm1      VARCHAR(20)   NOT NULL, -- 주담당자 이름1 (Not Null)
    empnm2      VARCHAR(20),            -- 주담당자 이름2
    phone1      VARCHAR(30)   NOT NULL, -- 주담당자 전화번호1 (Not Null)
    phone2      VARCHAR(30),            -- 부담당자 전화번호2
    faxno1      VARCHAR(30),            -- 팩스번호1
    
    -- 계정 및 시스템 정보
    login       VARCHAR(10),            -- 로그인ID
    pwdcd       VARCHAR(30),            -- 비밀번호
    mocomcd     VARCHAR(10),            -- 모회사 CD
    
    -- 서비스 상품 정보
    package     VARCHAR(3)    NOT NULL, -- 상품코드 (Not Null)
    packnm      VARCHAR(20),            -- 상품명
    
    -- 시스템 옵션 및 사용 여부
    taxinv      CHAR(1)       DEFAULT 'Y', -- 세금계산서 발행여부
    taxcnt      NUMERIC(6),                -- 매출세발건수
    corpcard    CHAR(1)       DEFAULT 'Y', -- 카드 사용여부
    corpcnt     NUMERIC(5),                -- 카드 사용 개수
    bizyn       CHAR(1)       DEFAULT 'N', -- 휴폐업여부
    scrapyn     CHAR(1)       DEFAULT 'N', -- 스크래핑여부
    curren      VARCHAR(5)    NOT NULL,    -- 회사사용 통화 (Not Null)
    owrate      CHAR(1)       DEFAULT 'X', -- 회사자체의 환율 사용여부
    budget      CHAR(1)       DEFAULT 'X', -- 예산기능 사용여부

    -- 제약 조건 설정
    CONSTRAINT pk_t_company PRIMARY KEY (comcd)
);

-- 테이블 주석 추가
COMMENT ON TABLE t_company IS '다봄 회계시스템 고객사 마스터 정보 테이블';