@echo off
cd /d "%~dp0"
"%~dp0venv\Scripts\python.exe" webapp\server.py
if errorlevel 1 pause
