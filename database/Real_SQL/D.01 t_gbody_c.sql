-- [거래] GL 보조부 미결 정리(Cleared) 테이블
CREATE TABLE t_gbody_c (
    -- 기본키 (PK) 구성
    comcd       VARCHAR(10)   NOT NULL, -- 회사코드
    glmaster    VARCHAR(6)    NOT NULL, -- GL계정코드
    mulky       CHAR(1)       NOT NULL, -- Multi Key (Down pay. 등)
    clrdt       DATE          NOT NULL, -- Clearing Date (정산 완료일)
    clrdoc      VARCHAR(10)   NOT NULL, -- Clearing 전표번호
    fisyr       NUMERIC(4)    NOT NULL, -- 회계연도 (원전표 기준)
    docno       VARCHAR(10)   NOT NULL, -- 전표번호 (원전표 기준)
    lineno      NUMERIC(4)    NOT NULL, -- 라인아이템번호
    
    -- 전표 기본 정보
    docty       CHAR(2)       NOT NULL, -- 전표종류
    invdt       DATE          NOT NULL, -- 증빙일자
    posdt       DATE          NOT NULL, -- 전기일자
    nodate      DATE,                   -- 정규전표 생성일
    reftx       VARCHAR(20),            -- 참조내역
    
    -- 부분 반제 정보
    pclrdoc     VARCHAR(10),            -- 부분반제 전표번호
    pclryr      NUMERIC(4),             -- 부분반제 년도
    pclrlin     NUMERIC(4),             -- 부분반제 라인
    
    -- 관리 및 분석 항목
    curren      CHAR(3)       NOT NULL, -- 통화 (전역규칙 적용)
    bookey      CHAR(2),                -- 장부키
    prjno       VARCHAR(30),            -- 프로젝트 번호
    debcre      CHAR(1)       NOT NULL, -- 차대구분지시자 (D/C)
    cscode      NUMERIC(6),             -- Customer/Supplier Code
    taxcd       CHAR(2),                -- 세금코드
    
    -- 지급 및 만기 관리
    pmethod     VARCHAR(20),            -- 지급방법
    pblck       CHAR(1),                -- 지불보류
    pterm       CHAR(4),                -- 지급조건
    basedt      DATE,                   -- 기산일
    dueday      NUMERIC(3),             -- 만기일수
    duedt       DATE,                   -- 만기일
    
    -- 금액 정보 (소수점 2자리 포함)
    bizamt      NUMERIC(25, 2) DEFAULT 0, -- 거래통화금액
    locamt      NUMERIC(25, 2) DEFAULT 0, -- 로컬통화금액
    biztax      NUMERIC(25, 2) DEFAULT 0, -- 거래통화 세액
    loctax      NUMERIC(25, 2) DEFAULT 0, -- 로컬통화 세액
    
    -- 조직 및 분석 항목
    pbank       NUMERIC(4),             -- 지급 Bank Key
    bizcat      CHAR(4),                -- 사업구분
    pctrcd      CHAR(5),                -- 손익부서코드
    cctrcd      CHAR(5),                -- 비용부서코드

    reg_date    TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

    -- 복합 기본키 설정
    CONSTRAINT pk_t_gbody_c PRIMARY KEY (
        comcd, glmaster, mulky, clrdt, clrdoc, fisyr, docno, lineno
    )
);

COMMENT ON TABLE t_gbody_c IS 'GL 보조부 미결 정리 데이터 테이블';