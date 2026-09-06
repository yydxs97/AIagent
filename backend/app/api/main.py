from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.models.schemas import TripPlan, TripPlanRequest
from app.agents.graph import create_trip_graph

import logging

# 基础配置：输出到控制台 + 文件
logging.basicConfig(
    level=logging.INFO,  # 设置最低捕获级别
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(),          # 1. 输出到控制台
        logging.FileHandler("app.log", encoding="utf-8")  # 2. 同时写入文件
    ]
)
app = FastAPI(
    title="智能旅行助手 LangGraph 最小版",
    description="使用 LangGraph 重构智能旅行助手的最小可运行版本",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

trip_graph = create_trip_graph()
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import logging
logger = logging.getLogger(__name__)


# 👇 核心：拦截所有 422 校验错误，并打印出前端到底传错了什么
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError):
    # 1. 打印前端实际发来的“错误”数据
    logger.error(f"❌ 前端传来的数据有误 (422 错误): {exc.body}")
    # 2. 打印具体是哪个字段错了
    logger.error(f"❌ 具体校验失败详情: {exc.errors()}")

    # 3. 返回标准的 422 响应给前端
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": str(exc.body)},
    )
@app.get("/")
def root():
    return {
        "message": "智能旅行助手 LangGraph 后端运行成功",
        "docs": "/docs",
        "api": "/api/trip/plan",
    }



@app.post("/api/trip/plan", response_model=TripPlan)
async def create_trip_plan(request: TripPlanRequest):
    """
    创建旅行计划。

    原项目：
    TripPlannerAgent.plan_trip(request)

    LangGraph 版本：
    trip_graph.invoke(initial_state)
    """

    initial_state = {
        "request": request.model_dump(),
        "dates": [],
        "attractions": [],
        "weather": [],
        "hotels": [],
        "plan": {},
    }

    result = await trip_graph.ainvoke(initial_state)
    return result["plan"]

