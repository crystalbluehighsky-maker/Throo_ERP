	T_TOTLG	레저 월별 토탈			*녹색필드별로 subtotal 해서 insert 또는 업데이트	
Key여부	Field	Field name	Type	길이 	샘플	
pk	fisyr	회계기간	number	4	2025	
pk	comcd	회사코드	varchar2	10	1091264100	
pk	debcre	차대구분지시자	char	1	D : Debit(차변), C : Credit(대변)	
	glmaster	GL계정코드	num	6	111111	
	bizcat	사업구분	char	4	설정나름,  1000:재무팀,2000:영업1팀등. 손익부서등에 의해 영향을 받을것임	
	pctrcd	손익부서코드	varchar2	10	손익부서	
	cctrcd	비용부서 코드	varchar2	10	비용부서	
	prjno	프로젝트 번호	varchar2	30	프로젝트 번호	*20250714-001
	macarea	제조관련기능영역	number	4	향후 제조관련 비용 구분위해	
	anakey	분석키	char	6	0001(제품A), 0002(제품B)	
	curren	통화	varchar2	5	KRW	
	ledger	원장	char	2	TL	
	trscr	거래통화 발란스 캐리포워드	number	25	소수점 2자리 포함	
	trs01	1월 거래통화	number	25	소수점 2자리 포함	
	trs02	2월 거래 통화	number	25	소수점 2자리 포함	
	trs03	3월 거래통화	number	25	소수점 2자리 포함	
	trs04	4월 거래 통화	number	25	소수점 2자리 포함	
	trs05	5월 거래통화	number	25	소수점 2자리 포함	
	trs06	6월 거래 통화	number	25	소수점 2자리 포함	
	trs07	7월 거래통화	number	25	소수점 2자리 포함	
	trs08	8월 거래 통화	number	25	소수점 2자리 포함	
	trs09	9월 거래통화	number	25	소수점 2자리 포함	
	trs10	10월 거래 통화	number	25	소수점 2자리 포함	
	trs11	11월 거래통화	number	25	소수점 2자리 포함	
	trs12	12월 거래 통화	number	25	소수점 2자리 포함	
	trs13	13월 거래통화	number	25	소수점 2자리 포함	
	trs14	14월 거래 통화	number	25	소수점 2자리 포함	
	trs15	15월 거래통화	number	25	소수점 2자리 포함	
	loccr	로컬통화 발란스 캐리포워드	number	25	소수점 2자리 포함	
	loc01	1월 로컬통화	number	25	소수점 2자리 포함	
	loc02	2월 로컬 통화	number	25	소수점 2자리 포함	
	loc03	3월 로컬통화	number	25	소수점 2자리 포함	
	loc04	4월 로컬 통화	number	25	소수점 2자리 포함	
	loc05	5월 로컬통화	number	25	소수점 2자리 포함	
	loc06	6월 로컬 통화	number	25	소수점 2자리 포함	
	loc07	7월 로컬통화	number	25	소수점 2자리 포함	
	loc08	8월 로컬 통화	number	25	소수점 2자리 포함	
	loc09	9월 로컬통화	number	25	소수점 2자리 포함	
	loc10	10월 로컬 통화	number	25	소수점 2자리 포함	
	loc11	11월 로컬통화	number	25	소수점 2자리 포함	
	loc12	12월 로컬 통화	number	25	소수점 2자리 포함	
	loc13	13월 로컬통화	number	25	소수점 2자리 포함	
	loc14	14월 로컬 통화	number	25	소수점 2자리 포함	
	loc15	15월 로컬통화	number	25	소수점 2자리 포함	
