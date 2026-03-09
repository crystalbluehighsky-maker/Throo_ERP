# serverlogin.py
import uvicorn
import sys, os
import json

# 프로젝트 루트(상위 폴더)를 sys.path에 추가하여 모듈 인식 가능하게 함
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

from fastapi import FastAPI, Form, Request, HTTPException, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
import jwt
from datetime import datetime, timedelta
from api.mainai import router as ai_router

app = FastAPI(title="Dabom ERP AI System")

# templates 폴더 절대 경로 설정
templates_dir = os.path.join(BASE_DIR, "templates")
templates = Jinja2Templates(directory=templates_dir)
SECRET_KEY = "dabom_super_secret_key_for_jwt"
ALGORITHM = "HS256"

mock_db_t_cuserinfo = {"user01": {"userid": "user01", "comcd": "1091264100", "username": "홍길동"}}

@app.get("/")
@app.get("/login")
async def show_login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/api/login")
async def login_process(userid: str = Form(...), password: str = Form("")):
    user_info = mock_db_t_cuserinfo.get(userid)
    if not user_info: return RedirectResponse(url="/login", status_code=303)
    payload = {"userid": userid, "comcd": user_info["comcd"], "username": user_info["username"], "exp": datetime.utcnow() + timedelta(minutes=60)}
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    response = RedirectResponse(url=f"/{user_info['comcd']}/dashboard", status_code=303)
    response.set_cookie(key="dabom_session", value=token, httponly=True)
    return response

@app.get("/{comcd}/dashboard")
async def show_dashboard(request: Request, comcd: str):
    token = request.cookies.get("dabom_session")
    if not token: return RedirectResponse(url="/login")
    try:
        decoded = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return templates.TemplateResponse("dashboard.html", {"request": request, "user_info": decoded})
    except: return RedirectResponse(url="/login")

@app.get("/aipost.html")
async def show_aipost(request: Request):
    # JSON 파일에서 예제 데이터 로드
    example_path = os.path.join(BASE_DIR, "prompts", "examples.json")
    examples = {}
    if os.path.exists(example_path):
        try:
            with open(example_path, "r", encoding="utf-8") as f:
                examples = json.load(f)
        except Exception as e:
            print(f"[ERROR] Failed to load examples.json: {e}")

    return templates.TemplateResponse("aipost.html", {"request": request, "examples": examples})

app.include_router(ai_router)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)