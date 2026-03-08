t_company 의 상품 컬럼과 t_package의 외래키를 설정함.

-- t_company의 package 컬럼을 t_package의 packcd와 연결 (외래키 설정)
ALTER TABLE t_company 
ADD CONSTRAINT fk_company_package FOREIGN KEY (package) 
REFERENCES t_package (packcd);