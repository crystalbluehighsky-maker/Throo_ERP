-- T_Nauth 마스터 등록
INSERT INTO t_nauth (seq, btype, mtype, name) VALUES (0.10, 'M-COM', 'M-COM-01', '회사등록');
INSERT INTO t_nauth (seq, btype, mtype, name) VALUES (0.11, 'M-COM', 'M-COM-02', '회사변경');
INSERT INTO t_nauth (seq, btype, mtype, name) VALUES (0.12, 'M-COM', 'M-COM-03', '회사조회');

-- 특정 사용자(LoginID)에게 '조회' 권한만 부여
INSERT INTO t_cauth (login, seq, btype, mtype, name, use_yn) 
VALUES ('user01', 0.12, 'M-COM', 'M-COM-03', '조회 전용', 'Y');

-- 특정 사용자에게 '등록' 권한 부여
INSERT INTO t_cauth (login, seq, btype, mtype, name, use_yn) 
VALUES ('admin', 0.10, 'M-COM', 'M-COM-01', '등록 권한', 'Y');
