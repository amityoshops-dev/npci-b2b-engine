# -*- coding: utf-8 -*-
import os
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from app.routers import rails

app = FastAPI(title="NPCI Clean Engine")

app.include_router(rails.router)

@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(current_dir, "templates", "index.html"),
        os.path.join(current_dir, "dashboard.html"),
        os.path.join(os.path.dirname(current_dir), "dashboard.html")
    ]
    for path in candidates:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Dashboard Template Not Found</h1>", status_code=404)
