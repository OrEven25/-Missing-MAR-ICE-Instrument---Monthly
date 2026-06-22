"""
CubeLogic DQE — Navigation entry point.
"""
import streamlit as st

st.set_page_config(
    page_title="CubeLogic DQE",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

pages = [
    st.Page("pages/3_Data_Overview.py",   title="Data Overview",    icon="📋"),
    st.Page("pages/0_Overview.py",        title="Overview",         icon="🏠"),
    st.Page("pages/1_Data_Stats.py",      title="Data Stats",       icon="📊"),
    st.Page("pages/4_Trading_Activity.py",title="Trading Activity", icon="📈"),
    st.Page("pages/2_Test_Cases.py",      title="Test Cases",       icon="🧪"),
]

pg = st.navigation(pages)
pg.run()
