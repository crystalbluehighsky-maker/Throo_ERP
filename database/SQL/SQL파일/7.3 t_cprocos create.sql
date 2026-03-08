-- [마스터] 손익부서(Profit Center) 마스터 테이블
CREATE TABLE t_cprocos (
    comcd       VARCHAR(10)   NOT NULL, -- 회사코드
    pctrcd      VARCHAR(10)   NOT NULL, -- 손익부서코드
    
    prcrnm      VARCHAR(30)   NOT NULL, -- 손익부서명
    pctrman     VARCHAR(20),            -- 담당자
    upteam      VARCHAR(10),            -- 상위부서
    prcrcat     CHAR(1),                -- 손익부서 카테고리
    bizcat      CHAR(4),                -- 사업구분
    macarea     NUMERIC(4, 0),          -- 제조영역
    currency    CHAR(3)       DEFAULT 'KRW', -- [전역규칙] 통화키 표준 적용
    
    -- [중요] 부모 테이블과 연결하기 위해 ana_type 추가
    ana_type    VARCHAR(10)   NOT NULL, -- 분석키 유형 (예: 'PROD')
    manaky      CHAR(8),                -- 중분류 분석키
    banaky      CHAR(8),                -- 대분류 분석키
    
    reg_date    TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

    -- PK 설정
    CONSTRAINT pk_t_cprocos PRIMARY KEY (comcd, pctrcd),
    
    -- FK 1: 회사 마스터 참조
    CONSTRAINT fk_cprocos_company FOREIGN KEY (comcd) 
        REFERENCES t_company (comcd) ON DELETE CASCADE,
        
    -- [수정] FK 2&3: ana_type을 포함하여 3개 컬럼으로 참조 (에러 해결 포인트!)
    CONSTRAINT fk_cprocos_mbkey FOREIGN KEY (comcd, ana_type, manaky) 
        REFERENCES t_mbkey (comcd, ana_type, manaky),
        
    CONSTRAINT fk_cprocos_cbkey FOREIGN KEY (comcd, ana_type, banaky) 
        REFERENCES t_cbkey (comcd, ana_type, banaky)
);

COMMENT ON TABLE t_cprocos IS '다봄 고객사별 손익부서 마스터 (분석키 연동 버전)';