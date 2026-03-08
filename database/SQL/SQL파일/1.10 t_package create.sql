-- [마스터] 다봄 서비스 상품 마스터 테이블
CREATE TABLE t_package (
    packcd      VARCHAR(10)   NOT NULL, -- 상품코드 (예: AAB, BASIC, PREM)
    packnm      VARCHAR(50)   NOT NULL, -- 상품명 (예: 베이직, 프리미엄)
    
    -- 서비스 제약 조건
    max_user    INTEGER       DEFAULT 5,    -- 최대 생성 가능 유저 수
    price       NUMERIC(15, 0) DEFAULT 0,   -- 월 이용료
    
    -- 상품 상태
    use_yn      CHAR(1)       DEFAULT 'Y', 
    descr       TEXT,                      
    reg_date    TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT pk_t_package PRIMARY KEY (packcd)
);

-- 초기 데이터 입력 (현재 사용 중이신 'AAB'를 포함)
INSERT INTO t_package (packcd, packnm, max_user, price, descr) 
VALUES ('AAB', '기본 고급형', 10, 50000, '현재 제공 중인 기본 패키지');

INSERT INTO t_package (packcd, packnm, max_user, price, descr) 
VALUES ('BASIC', '베이직', 5, 30000, '소규모 사업자용');