--이 테이블은 전표를 입력할때 AR,AP,GL에 입력되는 테이블종류를 관리하는 테이블이다.

-- [마스터] 전표 유형별 포스팅 대상 테이블 매핑 마스터
CREATE TABLE t_post_map (
    docty           CHAR(2)       NOT NULL, -- 전표 유형 (AR: 매출, AP: 매입, GL: 일반)
    target_table    VARCHAR(30)   NOT NULL, -- 데이터를 입력할 대상 테이블명
    required_yn     CHAR(1)       DEFAULT 'Y', -- 필수 입력 여부 (Y: 필수, N: 조건부/옵션)
    seq             NUMERIC(2)    NOT NULL, -- 실행/처리 순서
    description     VARCHAR(100),           -- 규칙 설명
    
    reg_date        TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,

    -- PK: 전표 유형 + 대상 테이블 조합
    CONSTRAINT pk_t_post_map PRIMARY KEY (docty, target_table)
);

COMMENT ON TABLE t_post_map IS '다봄 포스팅 엔진: 전표 유형별 저장 대상 테이블 매핑 규칙';