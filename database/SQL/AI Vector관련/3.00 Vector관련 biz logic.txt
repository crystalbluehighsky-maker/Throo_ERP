사용자 습관 먼저 찾기 (User Learn):

SQL
-- 사용자의 과거 수정 이력 중 가장 비슷한 1건을 찾음
SELECT final_json 
FROM t_v_user_learn 
ORDER BY embedding <=> '[사용자입력벡터]' 
LIMIT 1;
없다면 표준 패턴 찾기 (Std Pattern):

SQL
-- 표준 회계 패턴 중 가장 비슷한 1건을 찾음
SELECT docty, journal_json 
FROM t_v_std_pattern 
ORDER BY embedding <=> '[사용자입력벡터]' 
LIMIT 1;