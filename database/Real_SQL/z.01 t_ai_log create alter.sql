-- 2. t_ai_log: 어떤 패턴이 사용되었는지 추적하기 위한 컬럼 추가
-- (t_v_std_pattern의 id와 연결됩니다)
ALTER TABLE t_ai_log 
ADD COLUMN pattern_id INTEGER;