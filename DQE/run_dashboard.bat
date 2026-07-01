@echo off
cd /d C:\Users\or.even\DQE
echo Starting DQE Dashboard...
start "" http://localhost:8501
streamlit run dashboard/app.py
