-- t_nglmst의 모든 데이터를 t_cglmst로 복사 (회사코드 '1091264100' 기준)
INSERT INTO t_cglmst (
    comcd, glmaster, glname1, glname2, indbspl, currency, 
    balyn, taxtype, notxpt, eachitem, gltype, autopost, use_yn
)
SELECT 
    '1091264100', -- 여기에 복사 대상 회사코드를 직접 입력하세요
    glmaster, glname1, glname2, indbspl, currency, 
    balyn, taxtype, notxpt, eachitem, gltype, autopost, 'Y'
FROM t_nglmst;