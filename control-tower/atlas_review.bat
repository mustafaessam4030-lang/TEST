@echo off
REM ATLAS learning review: status, review, learned, proposals, approve ID --by NAME
cd /d "%~dp0"
python -m intelligence.review %*
pause
