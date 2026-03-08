-- [거래] 원장 헤더 테이블
CREATE TABLE t_lhead (
    -- 기본키 (PK) 구성
    comcd       VARCHAR(10)   NOT NULL, -- 회사코드
    docno       VARCHAR(10)   NOT NULL, -- 전표번호
    fisyr       NUMERIC(4)    NOT NULL, -- 회계연도 (예: 2025)
    
    -- 전표 기본 정보
    docty       CHAR(2)       NOT NULL, -- 전표종류 (t_cdocnum.doctype 연동)
    invdt       DATE          NOT NULL, -- 증빙일자
    posdt       DATE          NOT NULL, -- 전기일자 (실제 회계 반영일)
    period      NUMERIC(2)    NOT NULL, -- 기간 (01~12)
    trandt      DATE,                   -- 환산일자 (외화 평가 시 기준일)
    
    -- 참조 및 텍스트
    reftx       VARCHAR(20),            -- 참조내역
    refdoc      VARCHAR(20),            -- 참조전표번호
    infdoc      VARCHAR(20),            -- Interface 전표번호
    hdtxt       VARCHAR(30),            -- 헤더 텍스트 (전표 적요)
    
    -- 역분개 정보
    revdoc      VARCHAR(10),            -- 역분개전표 번호
    revyr       NUMERIC(4),             -- 역분개 회계연도
    
    -- 통화 및 환율 (전역 규칙 적용)
    curren      CHAR(3)       DEFAULT 'KRW', -- 통화키
    exrate      NUMERIC(9, 5) DEFAULT 0,     -- 환율
    
    -- 전표 상태 및 추적
    dstat       CHAR(1)       DEFAULT 'P',   -- 전표상태 (P:임시, N:정규)
    padate      DATE,                        -- 임시전표 생성일
    nodate      DATE,                        -- 정규전표 생성일
    
    -- 세금계산서 연동
    txivno      NUMERIC(30),            -- Tax Invoice 발행번호
    txyear      NUMERIC(4),             -- Tax Invoice 발행년도
    
    -- 시스템 기록
    entdt       DATE          DEFAULT CURRENT_DATE,      -- 입력일
    enttm       TIME          DEFAULT CURRENT_TIME,      -- 입력시간
    usrnm       VARCHAR(10),                             -- 입력 ID

    -- 제약 조건
    CONSTRAINT pk_t_lhead PRIMARY KEY (comcd, docno, fisyr),
    CONSTRAINT fk_lhead_company FOREIGN KEY (comcd) REFERENCES t_company (comcd)
);

COMMENT ON TABLE t_lhead IS '원장 헤더 테이블 (Journal Header)';