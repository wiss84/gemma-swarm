@echo off
echo Starting backend...
CALL "C:\Users\sunfi\anaconda3\Scripts\activate.bat" gemma_swarm
cd /d "C:\Users\sunfi\Desktop\gemma_swarm\"
python slack_app.py
pause
