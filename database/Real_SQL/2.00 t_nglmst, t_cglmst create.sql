DROP TABLE IF EXISTS t_nglmst CASCADE;

CREATE TABLE t_nglmst (
    -- VARCHAR(6)로 설정하여 6자리 코드 보존
    glmaster    VARCHAR(6)    NOT NULL, 
    glname1     VARCHAR(20)   NOT NULL,
    glname2     VARCHAR(50),
    indbspl     CHAR(1)       NOT NULL, -- B: BS, P: PL
    currency    CHAR(3)       DEFAULT 'KRW',
    balyn       CHAR(1)       DEFAULT 'N',
    taxtype     CHAR(1),
    notxpt      CHAR(1)       DEFAULT 'X',
    eachitem    CHAR(1),
    gltype      CHAR(1),
    autopost    CHAR(1),
    reg_date    TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT pk_t_nglmst PRIMARY KEY (glmaster),
    -- 데이터 청결을 위해 숫자만 입력되도록 강제 (선택사항)
    CONSTRAINT ck_glmaster_numeric CHECK (glmaster ~ '^[0-9]+$')
);

COMMENT ON TABLE t_nglmst IS '나린 표준 GL 계정 마스터 (템플릿)';

-- 고객GL master

DROP TABLE IF EXISTS t_cglmst CASCADE;

CREATE TABLE t_cglmst (
    comcd       VARCHAR(10)   NOT NULL, -- 회사코드
    glmaster    VARCHAR(6)    NOT NULL, -- 표준 GL 계정 코드
    
    -- 표준에서 복사해오지만 회사마다 이름을 다르게 쓸 수 있으므로 필드 유지
    glname1     VARCHAR(20)   NOT NULL, 
    glname2     VARCHAR(50),
    
    -- 속성 정보 (표준과 동일한 구조)
    indbspl     CHAR(1)       NOT NULL,
    currency    CHAR(3)       DEFAULT 'KRW',
    balyn       CHAR(1)       DEFAULT 'N',
    taxtype     CHAR(1),
    notxpt      CHAR(1)       DEFAULT 'X',
    eachitem    CHAR(1),
    gltype      CHAR(1),
    autopost    CHAR(1),
    use_yn      CHAR(1)       DEFAULT 'Y', -- 사용 여부 추가
    reg_date    TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

    -- PK: 어느 회사의 어느 계정인지 식별
    CONSTRAINT pk_t_cglmst PRIMARY KEY (comcd, glmaster),
    
    -- FK 1: 존재하는 회사여야 함
    CONSTRAINT fk_cglmst_company FOREIGN KEY (comcd) 
        REFERENCES t_company (comcd) ON DELETE CASCADE,
        
    -- FK 2: 반드시 표준 GL 마스터에 등록된 계정만 가져올 수 있음 (중요!)
    CONSTRAINT fk_cglmst_nglmst FOREIGN KEY (glmaster) 
        REFERENCES t_nglmst (glmaster)
);

COMMENT ON TABLE t_cglmst IS '다봄 고객사별 실제 사용 GL 계정 마스터';
