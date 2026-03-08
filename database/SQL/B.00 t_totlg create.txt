CREATE TABLE t_totlg (
    -- PK 및 주요 식별 정보
    comcd       VARCHAR(10) NOT NULL,       -- 회사코드 (PK)
    fisyr       NUMERIC(4)  NOT NULL,       -- 회계기간 (PK)
    debcre      CHAR(1)     NOT NULL,       -- 차대구분지시자 (D:차변, C:대변) (PK)
    glmaster    NUMERIC(6)  NOT NULL,       -- GL계정코드
    bizcat      CHAR(4),                    -- 사업구분
    pctrcd      VARCHAR(10),                -- 손익부서코드
    cctrcd      VARCHAR(10),                -- 비용부서 코드
    prjno       VARCHAR(30),                -- 프로젝트 번호
    macarea     NUMERIC(4),                 -- 제조관련기능영역
    anakey      CHAR(6),                    -- 분석키
    currency    CHAR(3)     NOT NULL,       -- 통화 (기존 curren에서 다봄 표준 규칙으로 변경)
    ledger      CHAR(2)     DEFAULT 'TL',   -- 원장
    
    -- 거래통화(Transaction Currency) 금액 필드 (Carry Forward + 1~15월)
    trscr       NUMERIC(25, 2) DEFAULT 0,   -- 거래통화 발란스 캐리포워드
    trs01       NUMERIC(25, 2) DEFAULT 0, trs02       NUMERIC(25, 2) DEFAULT 0,
    trs03       NUMERIC(25, 2) DEFAULT 0, trs04       NUMERIC(25, 2) DEFAULT 0,
    trs05       NUMERIC(25, 2) DEFAULT 0, trs06       NUMERIC(25, 2) DEFAULT 0,
    trs07       NUMERIC(25, 2) DEFAULT 0, trs08       NUMERIC(25, 2) DEFAULT 0,
    trs09       NUMERIC(25, 2) DEFAULT 0, trs10       NUMERIC(25, 2) DEFAULT 0,
    trs11       NUMERIC(25, 2) DEFAULT 0, trs12       NUMERIC(25, 2) DEFAULT 0,
    trs13       NUMERIC(25, 2) DEFAULT 0, trs14       NUMERIC(25, 2) DEFAULT 0,
    trs15       NUMERIC(25, 2) DEFAULT 0,

    -- 로컬통화(Local Currency) 금액 필드 (Carry Forward + 1~15월)
    loccr       NUMERIC(25, 2) DEFAULT 0,   -- 로컬통화 발란스 캐리포워드
    loc01       NUMERIC(25, 2) DEFAULT 0, loc02       NUMERIC(25, 2) DEFAULT 0,
    loc03       NUMERIC(25, 2) DEFAULT 0, loc04       NUMERIC(25, 2) DEFAULT 0,
    loc05       NUMERIC(25, 2) DEFAULT 0, loc06       NUMERIC(25, 2) DEFAULT 0,
    loc07       NUMERIC(25, 2) DEFAULT 0, loc08       NUMERIC(25, 2) DEFAULT 0,
    loc09       NUMERIC(25, 2) DEFAULT 0, loc10       NUMERIC(25, 2) DEFAULT 0,
    loc11       NUMERIC(25, 2) DEFAULT 0, loc12       NUMERIC(25, 2) DEFAULT 0,
    loc13       NUMERIC(25, 2) DEFAULT 0, loc14       NUMERIC(25, 2) DEFAULT 0,
    loc15       NUMERIC(25, 2) DEFAULT 0,

    -- 기본 키 제약 조건
    CONSTRAINT pk_t_totlg PRIMARY KEY (fisyr, comcd, debcre, glmaster, bizcat, pctrcd, cctrcd, prjno, macarea, anakey, curren, ledger)
);

-- 코멘트 추가 (관리 용이성)
COMMENT ON TABLE t_totlg IS '레저 월별 토탈 집계 테이블';