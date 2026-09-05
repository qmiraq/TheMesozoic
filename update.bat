```bat
@echo off
setlocal

echo ========================================
echo Updating The Mesozoic bot and RCON API...
echo ========================================
echo.

cd /d C:\EvrimaBot\TheMesozoic

echo Creating backup folder...
mkdir backups 2>nul

echo Backing up bot files...
xcopy /E /I /Y bot backups\bot_backup >nul

echo.
echo Stopping services before update...
net stop EvrimaBot >nul 2>&1
net stop miniEniac-RCON >nul 2>&1

echo.
echo Pulling latest changes from Git...
git pull

if errorlevel 1 (
    echo.
    echo ERROR: Git pull failed.
    echo Services will be restarted with the previous version.
    call restart-all.bat
    pause
    exit /b 1
)

echo.
echo Updating Python packages...
call .venv\Scripts\activate
pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo ERROR: Python package installation failed.
    echo Services will be restarted with the previous version.
    call restart-all.bat
    pause
    exit /b 1
)

echo.
echo Rebuilding RCON API...
cd miniEniac-RCON

dotnet build -c Release

if errorlevel 1 (
    echo.
    echo ERROR: RCON API build failed.
    echo Services will be restarted with the previous version.
    cd ..
    call restart-all.bat
    pause
    exit /b 1
)

cd ..

echo.
echo Build successful.
echo Restarting services...
call restart-all.bat

echo.
echo ========================================
echo Update completed successfully.
echo ========================================

pause
endlocal
```
