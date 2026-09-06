import streamlit as st
import requests
import json

st.set_page_config(page_title="智能旅行助手", page_icon="✈️", layout="wide")

# 👇 修改为你的后端实际地址
BACKEND_URL = "http://localhost:8000/api/trip/plan"

# ========== 1. 参数输入表单 ==========
st.title("✈️ 智能旅行规划助手")

with st.sidebar:
    st.header("📝 规划参数")
    with st.form("plan_form"):
        city = st.text_input("目的地城市", value="成都")
        col1, col2 = st.columns(2)
        start_date = col1.date_input("开始日期", value="2026-09-05")
        end_date = col2.date_input("结束日期", value="2026-09-05")
        travel_days = st.number_input("旅行天数", min_value=1, max_value=30, value=1)
        accommodation = st.selectbox("住宿偏好", ["经济型酒店", "舒适型酒店", "豪华酒店", "民宿"])
        transportation = st.selectbox("交通方式", ["公共交通", "自驾", "打车为主", "步行+骑行"])
        preferences = st.multiselect("兴趣偏好", ["历史文化", "美食", "自然风光", "亲子", "购物", "夜生活"],
                                     default=["历史文化", "美食"])
        free_text_input = st.text_area("补充需求", placeholder="例如：希望多安排一些博物馆")

        submitted = st.form_submit_button("🚀 生成旅行计划", use_container_width=True)

# ========== 2. 发起请求并缓存结果 ==========
if submitted:
    payload = {
        "city": city,
        "start_date": str(start_date),
        "end_date": str(end_date),
        "travel_days": travel_days,
        "accommodation": accommodation,
        "transportation": transportation,
        "preferences": preferences,
        "free_text_input": free_text_input
    }

    with st.spinner("⏳ AI 正在为您规划行程，请稍候..."):
        try:
            response = requests.post(BACKEND_URL, json=payload, timeout=300)
            response.raise_for_status()
            st.session_state["trip_plan"] = response.json()
            st.session_state["last_payload"] = payload  # 记录上次请求参数
        except requests.exceptions.ConnectionError:
            st.error("❌ 无法连接到后端服务，请确认后端已启动")
        except requests.exceptions.HTTPError as e:
            st.error(f"❌ 后端错误: {e.response.status_code} - {e.response.text}")
        except Exception as e:
            st.error(f"❌ 请求失败: {str(e)}")

# ========== 3. 展示旅行计划 ==========
if "trip_plan" in st.session_state:
    plan = st.session_state["trip_plan"]

    st.divider()
    st.title(f"✈️ {plan.get('city', '')} 旅行计划")
    st.caption(f"📅 {plan.get('start_date', '')} 至 {plan.get('end_date', '')}")

    # 顶部指标
    col1, col2, col3 = st.columns(3)
    weather = plan.get("weather_info", [{}])[0] if plan.get("weather_info") else {}
    budget = plan.get("budget", {})
    days = plan.get("days", [])

    with col1:
        st.metric("🌤️ 天气预报",
                  f"{weather.get('day_weather', '未知')} {weather.get('day_temp', '--')}°C",
                  f"夜间 {weather.get('night_temp', '--')}°C")
    with col2:
        st.metric("💰 预算总计", f"¥{budget.get('total', 0)}",
                  f"门票¥{budget.get('total_attractions', 0)} / 住宿¥{budget.get('total_hotels', 0)}")
    with col3:
        total_attr = sum(len(d.get("attractions", [])) for d in days)
        st.metric("📅 行程概览", f"{len(days)} 天", f"共 {total_attr} 个景点")

    st.divider()

    # AI建议
    suggestions = plan.get("overall_suggestions", "")
    if suggestions:
        st.subheader("💡 AI 贴心建议")
        for tip in [t.strip() for t in suggestions.split("\n") if t.strip()]:
            clean_tip = tip.split("：", 1)[-1].split(":", 1)[-1].strip()
            st.info(clean_tip)

    # 每日行程
    for day in days:
        day_num = day.get("day_index", 0) + 1
        st.header(f"📍 第 {day_num} 天 - {day.get('date', '')}")
        st.caption(
            f"**主题：** {day.get('description', '')} | 🚌 {day.get('transportation', '')} | 🏨 {day.get('accommodation', '')}")

        hotel = day.get("hotel")
        if hotel and hotel.get("name"):
            with st.container(border=True):
                h1, h2 = st.columns([3, 1])
                with h1:
                    st.markdown(f"**🏨 {hotel['name']}**")
                    st.caption(f"📍 {hotel.get('address', '')}")
                with h2:
                    st.metric("预估费用", f"¥{hotel.get('estimated_cost', 0)}")

        # 时间轴
        timeline = []
        breakfast = next((m for m in day.get("meals", []) if m["type"] == "breakfast"), None)
        if breakfast:
            timeline.append(("🍳 早餐", breakfast["name"], "", f"¥{breakfast.get('estimated_cost', 0)}", ""))
        for attr in day.get("attractions", []):
            timeline.append(("🏛️ 景点", attr["name"], attr.get("description", ""), f"¥{attr.get('ticket_price', 0)}",
                             attr.get("visit_duration", "")))
        for mt, lb in [("lunch", "🍜 午餐"), ("dinner", "🌙 晚餐")]:
            meal = next((m for m in day.get("meals", []) if m["type"] == mt), None)
            if meal:
                timeline.append((lb, meal["name"], "", f"¥{meal.get('estimated_cost', 0)}", ""))

        for tl, name, desc, cost, dur in timeline:
            with st.container(border=True):
                c1, c2 = st.columns([4, 1])
                with c1:
                    st.markdown(f"**{tl} | {name}**" + (f" ⏱️ {dur}" if dur else ""))
                    if desc: st.caption(desc)
                with c2:
                    st.caption(cost)
        st.divider()

    # 底部操作
    bc1, bc2 = st.columns(2)
    with bc1:
        if st.button("🔄 重新生成", use_container_width=True):
            st.session_state.pop("trip_plan", None)
            st.rerun()
    with bc2:
        md = [f"# {plan.get('city', '')} 旅行计划\n"]
        for d in days:
            md.append(f"## 第 {d['day_index'] + 1} 天 - {d['date']}\n")
            for a in d.get("attractions", []):
                md.append(f"- **{a['name']}** ({a.get('visit_duration', '')}) ¥{a.get('ticket_price', 0)}\n")
        st.download_button("📋 下载 Markdown", "\n".join(md), f"{plan.get('city', '')}_计划.md", "text/markdown",
                           use_container_width=True)

else:
    # 首次进入无数据时的提示
    if not submitted:
        st.info("👈 请在左侧填写参数并点击「生成旅行计划」")