t_ntxkey 테이블에 만들어놓고 실행

-- 특정 회사를 위한 세금 코드 일괄 생성 스크립트
INSERT INTO t_ctxkey (
    comcd, 
    taxcd, 
    taxnm, 
    taxtyp, 
    taxrate, 
    currency, 
    glmaster, 
    use_yn
)
SELECT 
    '1091264100', -- 대상 회사 코드 (본인의 회사 코드로 변경하세요)
    taxcd, 
    taxnm, 
    taxtyp, 
    taxrate, 
    currency, 
    glmaster, 
    'Y'           -- 사용 여부 기본값
FROM t_ntxkey;

-- 실행 후 확인
SELECT * FROM t_ctxkey WHERE comcd = '1091264100';