--소분류(stype) 컬럼이 추가된 최신 정의서를 확인했습니다. 요구하신 대로 기존 테이블을 안전하게 삭제하고, 소분류 컬럼 및 외래키(FK), 상태값 필드를 모두 반영한 재생성 SQL 문을 작성해 --드립니다.

--1. PostgreSQL 테이블 재생성 SQL
--이 스크립트는 T_Nauth를 먼저 생성한 후, 이를 참조하는 T_Cauth를 생성합니다. DROP TABLE 시 CASCADE 옵션을 사용하여 의존성이 있는 객체까지 깔끔하게 정리합니다.

--SQL 새로 테이블 생성 아니면 drop은 잘 보고 쓸지말지 결정!!!
-- 1. 기존 테이블 삭제 (의존성 포함 삭제)
DROP TABLE IF EXISTS t_cauth CASCADE;
DROP TABLE IF EXISTS t_nauth CASCADE;

-- 2. [마스터] 다봄 표준 권한 테이블 (T_Nauth) 생성
CREATE TABLE t_nauth (
    seq     NUMERIC(5, 2) NOT NULL,
    btype   CHAR(5)       NOT NULL,
    mtype   CHAR(8)       NOT NULL,
    stype   VARCHAR(13)   NOT NULL, -- 추가된 소분류 컬럼
    name    VARCHAR(20)   NOT NULL, -- 분류명 (회사업무명 등)
    
    -- PK 설정: 정의서에 따른 3개 복합키
    CONSTRAINT pk_t_nauth PRIMARY KEY (seq, btype, mtype)
);

-- 3. [마스터] 고객 ID별 권한 저장 테이블 (T_Cauth) 생성
CREATE TABLE t_cauth (
    login     VARCHAR(10)   NOT NULL, -- 로그인 ID
    seq       NUMERIC(5, 2) NOT NULL,
    btype     CHAR(5)       NOT NULL,
    mtype     CHAR(8)       NOT NULL,
    stype     VARCHAR(13),            -- 소분류
    name      VARCHAR(20),            -- 분류명
    use_yn    CHAR(1)       DEFAULT 'Y', -- 사용여부 (Y 또는 N)
    reg_date  TIMESTAMP     DEFAULT CURRENT_TIMESTAMP, -- 등록일시
    
    -- PK 설정: 정의서에 따른 4개 복합키
    CONSTRAINT pk_t_cauth PRIMARY KEY (login, seq, btype, mtype),
    
    -- 외래키 설정: T_Nauth의 마스터 정보와 연결
    CONSTRAINT fk_t_cauth_nauth FOREIGN KEY (seq, btype, mtype) 
        REFERENCES t_nauth (seq, btype, mtype) ON DELETE CASCADE
);

-- 인덱스 생성 (성능 최적화)
CREATE INDEX idx_t_cauth_login ON t_cauth(login);
CREATE INDEX idx_t_nauth_stype ON t_nauth(stype);
