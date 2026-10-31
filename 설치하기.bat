@echo off
REM Install dependencies and verify (messages are printed by Python in UTF-8)
cd /d "%~dp0"
python -m pip install -r requirements.txt
echo.
python -X utf8 test_dxf_layer_classifier.py
python -X utf8 test_rules_and_layout.py
echo.
pause
