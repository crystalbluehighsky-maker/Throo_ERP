-- 기존 테이블 삭제
DROP TABLE IF EXISTS t_cuserinfo CASCADE;

CREATE TABLE t_cuserinfo (
    comcd       VARCHAR(10)   NOT NULL, -- 회사코드 (PK)
    userid      VARCHAR(20)   NOT NULL, -- 사용자ID (PK)
    username    VARCHAR(50)   NOT NULL, -- 사용자이름 (usernm -> username 변경 반영)
    pwdcd       VARCHAR(255)  NOT NULL, -- 패스워드 (추천대로 암호화 고려 255자)
    deptcd      VARCHAR(20),            -- 부서코드
    empno       VARCHAR(10),            -- 사원번호 (NULL 허용)
    phone       VARCHAR(30),            -- 전화번호
    position    VARCHAR(20),            -- 직급
    
    -- 상태 및 권한
    use_yn      CHAR(1)       DEFAULT 'Y', 
    admin_yn    CHAR(1)       DEFAULT 'N', 
    reg_date    TIMESTAMP     DEFAULT CURRENT_TIMESTAMP, 

    -- [제약 조건 1] 기본키: 회사별로 유저 ID는 유일해야 함
    CONSTRAINT pk_t_cuserinfo PRIMARY KEY (comcd, userid),
    
    -- [제약 조건 2] 외래키: 반드시 존재하는 회사여야 함
    CONSTRAINT fk_cuserinfo_company FOREIGN KEY (comcd) 
        REFERENCES t_company (comcd) ON DELETE CASCADE,
        
    -- [제약 조건 3] 유니크: 사원번호가 입력될 경우, 해당 회사 내에서는 중복 금지
    -- (NULL 값은 중복 체크에서 제외되므로 소규모 회사 대응 가능)
    CONSTRAINT uk_t_cuserinfo_empno UNIQUE (comcd, empno)
);

-- 인덱스 추가 (조회 최적화)
CREATE INDEX idx_t_cuserinfo_dept ON t_cuserinfo(comcd, deptcd);

COMMENT ON TABLE t_cuserinfo IS '다봄 고객사 사용자 정보 (사원번호 선택적 유니크 적용)';