import logging
import json
import os
import re
from typing import TypedDict, List, Dict, Any
from datetime import datetime, timedelta
import asyncio
from langgraph.graph import StateGraph, START, END
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage
from app.models.schemas import TripPlan
from app.amap_service.nmap_service import get_mcp_client
from app.tools.config import get_settings

logger = logging.getLogger(__name__)
from dotenv import load_dotenv
load_dotenv()
# ==========================================
# 1. 状态机定义 (保持不变，但明确期望是 List[Dict])
# ==========================================
class TripGraphState(TypedDict):
    request: Dict[str, Any]
    dates: List[str]
    attractions: List[Dict[str, Any]]  # 期望存入结构化字典列表
    weather: List[Dict[str, Any]]  # 期望存入结构化字典列表
    hotels: List[Dict[str, Any]]  # 期望存入结构化字典列表
    plan: Dict[str, Any]

# ==========================================
# 2. 初始化 LLM
# ==========================================
settings = get_settings()

llm = ChatOpenAI(
    api_key = os.getenv("OPENAI_API_KEY1"),
    base_url = os.getenv("OPENAI_BASE_URL"),
    model=os.getenv("OPENAI_MODEL_NAME"),
    max_tokens=16384,
    timeout=300,
    extra_body={"enable_thinking": False} #结构化输出，关闭推荐，
)


_mcp_tools_cache = None
# ==========================================
# 全局 MCP 工具缓存 (核心优化：确保整个生命周期只启动一次进程)
# ==========================================
_mcp_init_lock = asyncio.Lock()  # 👈 新增：异步锁，防止并发重复初始化

async def get_shared_mcp_tools():
    """获取共享的 MCP 工具列表，确保只初始化一次"""
    print("🚨 🚨 🚨 异步锁版本已生效！ 🚨 🚨 🚨")  # 👈 加上这句
    global _mcp_tools_cache

    # 第一层检查：如果已经初始化，直接返回（无锁，高性能）
    if _mcp_tools_cache is not None:
        return _mcp_tools_cache

    # 第二层检查：加锁，确保高并发下只有一个协程能进入初始化逻辑
    async with _mcp_init_lock:
        # 拿到锁后，再次检查，防止在等待锁的过程中其他协程已经初始化完毕
        if _mcp_tools_cache is not None:
            return _mcp_tools_cache

        logger.info("🔌 首次初始化 MCP 客户端，正在启动高德地图服务 (仅需一次)...")
        try:
            client = get_mcp_client()
            # 👇 新增：加上 15 秒超时保护，防止子进程卡死导致整个服务挂起
            _mcp_tools_cache = await asyncio.wait_for(client.get_tools(), timeout=15.0)
            logger.info(f"✅ MCP 工具加载成功并缓存，共 {len(_mcp_tools_cache)} 个工具。后续节点将直接复用！")
        except asyncio.TimeoutError:
            logger.error("❌ MCP 工具初始化超时 (15秒)，可能是 stdio 进程卡死。将使用空工具列表降级继续。")
            _mcp_tools_cache = []
        except Exception as e:
            logger.error(f"❌ MCP 工具初始化彻底失败！错误信息: {e}", exc_info=True)
            _mcp_tools_cache = []  # 👈 降级处理：返回空列表，防止整个 Graph 崩溃

    return _mcp_tools_cache
# ==========================================
# 3. 优化后的 Prompts (核心修改：拥抱原生 Tool Calling，强制输出 JSON)
# ==========================================
SEARCH_ATTRACTION_PROMPT = """你是景点搜索专家。
你的任务：使用提供的 `amap_maps_text_search` 工具搜索符合用户偏好的景点。
【严格要求】：
1. 必须使用工具搜索，绝对不要编造景点。
2. 工具调用成功后，你的最终回复**必须且只能是一个 JSON 数组**，包含搜索到的景点信息。
3. 不要输出任何解释性文字、不要使用 Markdown 代码块标记，直接以 `[` 开头，以 `]` 结尾。
4. 确保每个景点对象包含：name, address, location (包含 longitude 和 latitude), ticket_price, description。
"""

SEARCH_WEATHER_PROMPT = """你是天气查询专家。
你的任务：使用提供的 `amap_maps_weather` 工具查询指定城市的天气。
【严格要求】：
1. 必须使用工具查询。
2. 工具调用成功后，你的最终回复**必须且只能是一个 JSON 数组**，包含每天的天气信息。
3. 不要输出任何解释性文字，直接以 `[` 开头，以 `]` 结尾。
4. 确保每个对象包含：date, day_weather, night_weather, day_temp (纯数字), night_temp (纯数字)。
"""

SEARCH_HOTEL_PROMPT = """你是酒店推荐专家。
你的任务：使用提供的 `amap_maps_text_search` 工具搜索符合用户预算和类型的酒店。
【严格要求】：
1. 必须使用工具搜索。
2. 工具调用成功后，你的最终回复**必须且只能是一个 JSON 数组**，包含推荐的酒店信息。
3. 不要输出任何解释性文字，直接以 `[` 开头，以 `]` 结尾。
4. 确保每个对象包含：name, address, location (包含 longitude 和 latitude), estimated_cost, rating。
"""

PLANNER_AGENT_PROMPT = """你是顶级行程规划专家。请根据以下信息，生成详细的旅行计划。

【严格要求 - 数据类型必须完全匹配，否则视为失败】：
1. "days" 必须是一个**数组 (List)**，包含每一天的详细行程对象，**绝对不能是整数**！
2. "overall_suggestions" 必须是一个**单一的字符串 (String)**。如果需要分条，请使用 "\\n" 换行，**绝对不能是数组 ([])**！
3. 预算对象必须严格命名为 "budget"，**不能是 "budget_summary"** 或其他名称。
4. 每天必须包含 "attractions" (景点列表) 和 "meals" (餐饮列表)。
5. 必须且只能输出合法的 JSON 格式数据，不要包含任何 Markdown 标记（如 ```json），不要有任何解释性文字！直接以 { 开头，以 } 结尾。

【标准 JSON 结构示例 (请严格模仿此结构)】：
{
  "city": "成都",
  "start_date": "2026-09-10",
  "end_date": "2026-09-10",
  "days": [
    {
      "date": "2026-09-10",
      "day_index": 0,
      "description": "第一天：历史文化探索",
      "transportation": "公共交通",
      "accommodation": "经济型酒店",
      "hotel": {"name": "OYO七里香大酒店", "address": "站北东街1号", "estimated_cost": 140},
        "attractions": [
        {"name": "文殊坊", "address": "白云寺街", "location": {"longitude": 104.07, "latitude": 30.67}, "ticket_price": 0, "visit_duration": "2小时", "description": "参观隋代始建的文殊院"}
      ],
      "meals": [
        {"type": "breakfast", "name": "甜水面", "estimated_cost": 20},
        {"type": "lunch", "name": "伤心凉粉", "estimated_cost": 60},
        {"type": "dinner", "name": "回锅肉", "estimated_cost": 70}
      ]
    }
  ],
  "weather_info": [
    {"date": "2026-09-10", "day_weather": "晴", "night_weather": "多云", "day_temp": 25, "night_temp": 18, "wind_direction": "南", "wind_power": "1-3级"}
  ],
  "overall_suggestions": "建议1：早点出发避开高峰。\\n建议2：带伞以防阵雨。\\n建议3：穿舒适运动鞋。",
  "budget": {
    "total_attractions": 0,
    "total_hotels": 140,
    "total_meals": 150,
    "total_transportation": 30,
    "total": 320
  }
}

【用户需求】
{request}

【可用景点】(JSON格式)
{attractions}

【天气信息】(JSON格式)
{weather}

【酒店信息】(JSON格式)
{hotels}
"""
# ==========================================
# 4. 节点实现 (核心修改：尝试解析 JSON，确保 State 存入结构化数据)
# ==========================================
def _parse_llm_json_response(content: str, fallback_name: str) -> Any:
    """辅助函数：尝试将 LLM 的回复解析为 JSON，失败则返回原始文本并警告"""
    try:
        # 移除可能存在的 ```json 和 ``` 标记
        clean_str = re.sub(r'^```json\s*|\s*```$', '', content.strip(), flags=re.MULTILINE)
        return json.loads(clean_str)
    except json.JSONDecodeError:
        logger.warning(f"⚠️ {fallback_name} 节点：LLM 未返回合法 JSON，降级为纯文本存储。内容前100字符: {content[:100]}")
        return content  # 降级处理，保证流程不中断


async def prepare_node(state: TripGraphState) -> Dict[str, Any]:
    """准备节点：生成日期列表"""
    request = state["request"]
    start_date = datetime.strptime(request["start_date"], "%Y-%m-%d")
    dates = [(start_date + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(request["travel_days"])]
    return {"dates": dates}


#跟agent自主决策有什么关系，有大量
async def search_attractions_node(state: TripGraphState) -> Dict[str, Any]:
    """景点搜索节点"""
    request = state["request"]
    # client = get_mcp_client()
    tools = await get_shared_mcp_tools()
    agent = create_react_agent(llm, tools, prompt=SEARCH_ATTRACTION_PROMPT)
    # ✅ 修改后（推荐）
    prefs_str = ", ".join(request['preferences']) if isinstance(request['preferences'], list) else request[
        'preferences']
    user_query = f"请搜索 {request['city']} 的 {prefs_str} 景点，大约需要 {request['travel_days']} 天的游览量。"
    result = await agent.ainvoke({"messages": [HumanMessage(content=user_query)]})
    raw_content = result["messages"][-1].content


    # ✅ 核心优化：尝试解析为 List[Dict]，让 State 存储结构化数据
    parsed_attractions = _parse_llm_json_response(raw_content, "景点搜索")
    return {"attractions": parsed_attractions}


async def search_weather_node(state: TripGraphState) -> Dict[str, Any]:
    """天气查询节点"""
    request = state["request"]
    dates = state["dates"]

    # client = get_mcp_client()
    tools = await get_shared_mcp_tools()
    agent = create_react_agent(llm, tools, prompt=SEARCH_WEATHER_PROMPT)
    user_query = f"请查询 {request['city']} 未来几天的天气，特别是这些日期: {', '.join(dates)}。"
    result = await agent.ainvoke({"messages": [HumanMessage(content=user_query)]})
    parsed_weather = _parse_llm_json_response(result["messages"][-1].content, "天气查询")
    return {"weather": parsed_weather}


async def search_hotels_node(state: TripGraphState) -> Dict[str, Any]:
    """酒店推荐节点"""
    request = state["request"]

    # client = get_mcp_client()
    tools = await get_shared_mcp_tools()
    agent = create_react_agent(llm, tools, prompt=SEARCH_HOTEL_PROMPT)
    user_query = f"请搜索 {request['city']} 的 {request['accommodation']} 酒店。"
    result = await agent.ainvoke({"messages": [HumanMessage(content=user_query)]})
    parsed_hotels = _parse_llm_json_response(result["messages"][-1].content, "酒店推荐")
    return {"hotels": parsed_hotels}

async def generate_plan_node(state: TripGraphState) -> Dict[str, Any]:
    request = state["request"]
    logger.info("🤖 Planner Agent 正在使用 LLM 生成行程 (纯 JSON 模式)...")
    try:
        # 👇 核心优化 1：强制精简数据，丢弃冗长的 description，防止 Token 爆炸导致 LLM 返回空
        def extract_core_info(items):
            if not isinstance(items, list): return []
            return [
                {
                    "name": item.get("name"),
                    "address": item.get("address"),
                    "location": item.get("location"),
                    "ticket_price": item.get("ticket_price"),
                    "visit_duration": item.get("visit_duration", "未指定")
                    # 故意丢弃 description，大幅减少 Token！
                }
                for item in items if isinstance(item, dict)
            ]

        core_attractions = extract_core_info(state.get("attractions", []))
        core_hotels = extract_core_info(state.get("hotels", []))
        weather_data = state.get("weather", [])

        # 👇 核心优化 2：使用精简后的数据替换占位符
        prompt_text = PLANNER_AGENT_PROMPT \
            .replace("{request}", json.dumps(request, ensure_ascii=False)) \
            .replace("{attractions}", json.dumps(core_attractions, ensure_ascii=False)) \
            .replace("{weather}", json.dumps(weather_data, ensure_ascii=False)) \
            .replace("{hotels}", json.dumps(core_hotels, ensure_ascii=False))

        logger.info(f"⏳ 正在等待 LLM 返回... (精简后 Prompt 长度: {len(prompt_text)} 字符)")
        response = await llm.ainvoke(prompt_text)
        # 安全获取 content，防止某些模型返回格式不同
        raw_content = response.content if hasattr(response, 'content') else str(response)
        logger.info(f"🔍 LLM 原始响应内容长度: {len(raw_content)}")

        # 👇 核心优化 3：严格检查是否为空
        if not raw_content or not raw_content.strip():
            logger.error("❌ LLM 返回了空内容！可能原因：1. API Key 余额不足 2. Prompt 仍超长被拒 3. 网络超时。")
            raise ValueError("LLM 返回了空内容，请检查 API 配置、账户余额或稍后重试。")

        logger.info(f"✅ LLM 返回成功！准备解析 JSON (长度: {len(raw_content)})")

        # 👇 核心优化 4：鲁棒的 JSON 提取（智能寻找 { 和 }）
        clean_str = re.sub(r'^\s*```(?:json)?\s*|\s*```\s*$', '', raw_content.strip(), flags=re.MULTILINE)
        start_idx = clean_str.find('{')
        end_idx = clean_str.rfind('}')

        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            clean_str = clean_str[start_idx:end_idx + 1]
        plan_dict = json.loads(clean_str)
        logger.debug(f"🔍 LLM 原始返回 (长度={len(clean_str)}): {clean_str}")
        # 尝试用 Pydantic 校验
        try:
            final_plan = TripPlan(**plan_dict)
            return {"plan": final_plan.model_dump()}
        except Exception as pydantic_err:
            logger.warning(f"⚠️ Pydantic 校验失败，但流程已跑通！直接返回原始字典。错误: {pydantic_err}")
            return {"plan": plan_dict}

    except json.JSONDecodeError as je:
        logger.error(f"❌ JSON 解析失败，LLM 返回的不是合法 JSON: {je}")
        logger.error(f"🔍 失败时的原始内容前 300 字符: {raw_content[:300]}")
        raise ValueError(f"JSON 解析失败: {je}")
    except Exception as e:
        logger.error(f"❌ LLM 生成计划失败: {e}", exc_info=True)
        raise ValueError(f"行程规划生成失败: {str(e)}")
# ==========================================
# 5. 构建并编译 Graph
# ==========================================
def create_trip_graph():
    graph = StateGraph(TripGraphState)

    graph.add_node("prepare", prepare_node)
    graph.add_node("search_attractions", search_attractions_node)
    graph.add_node("search_weather", search_weather_node)
    graph.add_node("search_hotels", search_hotels_node)
    graph.add_node("generate_plan", generate_plan_node)

    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "search_attractions")
    graph.add_edge("search_attractions", "search_weather")
    graph.add_edge("search_weather", "search_hotels")
    graph.add_edge("search_hotels", END)
    graph.add_edge("search_hotels", "generate_plan")
    graph.add_edge("generate_plan", END)

    return graph.compile()