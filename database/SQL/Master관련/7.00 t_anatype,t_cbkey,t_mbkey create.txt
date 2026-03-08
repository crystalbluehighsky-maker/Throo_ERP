-- [마스터] 분석키 유형 정의 (예: 제품별, 프로젝트별 등)
CREATE TABLE t_anatype (
    comcd       VARCHAR(10)   NOT NULL, -- 회사코드
    ana_type    VARCHAR(10)   NOT NULL, -- 유형코드 (예: PROD, PROJ, REGN)
    ana_name    VARCHAR(50)   NOT NULL, -- 유형명 (예: 제품별 손익, 프로젝트별 손익)
    level1_nm   VARCHAR(20)   DEFAULT '대분류', -- 화면에 표시할 1단계 라벨
    level2_nm   VARCHAR(20)   DEFAULT '중분류', -- 화면에 표시할 2단계 라벨
    
    CONSTRAINT pk_t_anatype PRIMARY KEY (comcd, ana_type)
);


-- 수정된 1단계 마스터
CREATE TABLE t_cbkey (
    comcd       VARCHAR(10)   NOT NULL,
    ana_type    VARCHAR(10)   NOT NULL, -- 추가: 어떤 유형의 분석키인가?
    banaky      CHAR(8)       NOT NULL,
    bananm      VARCHAR(30)   NOT NULL,
    CONSTRAINT pk_t_cbkey PRIMARY KEY (comcd, ana_type, banaky)
);

-- 수정된 2단계 마스터
CREATE TABLE t_mbkey (
    comcd       VARCHAR(10)   NOT NULL,
    ana_type    VARCHAR(10)   NOT NULL, -- 추가
    manaky      CHAR(8)       NOT NULL,
    mananm      VARCHAR(30)   NOT NULL,
    banaky      CHAR(8)       NOT NULL,
    CONSTRAINT pk_t_mbkey PRIMARY KEY (comcd, ana_type, manaky)
);