@echo off
rem
rem -n: pytest-xdistによる並列実行。pip install pytest-xdist
rem
title %~nx0
:Retry

cls
pytest tests_strict tests -n 4

pause
goto :Retry

