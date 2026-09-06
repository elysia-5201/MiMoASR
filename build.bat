@echo off
rem Rebuild MiMoASR (requires Python 3.12 + requirements.txt deps + pyinstaller).
rem NOTE: keep this file pure ASCII - cmd.exe parses batch files with the
rem local codepage (GBK on zh-CN), UTF-8 Chinese text breaks parsing.
cd /d "%~dp0"
pip install -r requirements.txt "pyinstaller>=6.0,<7.0"
python -m PyInstaller --noconfirm --onefile --windowed --name MiMoASR --icon icon.ico main.py
echo.
echo Done! Executable: dist\MiMoASR.exe
pause
