@echo off
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
cd /d "%~dp0"

set "APP_ROOT=%~dp0"
set "TTS_MODEL_DIR=%APP_ROOT%_model_cache\Qwen3-TTS-12Hz-1___7B-Base"
set "TTS_MODEL_FILE=%TTS_MODEL_DIR%\model.safetensors"
set "CONDA_ENV_NAME=videolingo"

set "MSG_TTS_B64=44CQ5o+Q56S644CRIOacquajgOa1i+WIsOacrOWcsCBRd2VuIFRUUyDmqKHlnovvvJoKICAgICAgIF9fTU9ERUxfRklMRV9fCuOAkOaPkOekuuOAkSDljp/lo7DlhYvpmobphY3pn7Plip/og73mmoLml7bml6Dms5Xkvb/nlKjjgIIKCuWPr+S7jiBNb2RlbFNjb3BlIOS4i+i9veaooeWei++8mgogIOaooeWei+WQje+8mlF3ZW4vUXdlbjMtVFRTLTEySHotMS43Qi1CYXNlCiAg55uu5qCH55uu5b2V77yaX19NT0RFTF9ESVJfXwogIOS4i+i9veWRveS7pO+8mgogIG1vZGVsc2NvcGUgZG93bmxvYWQgLS1tb2RlbCBRd2VuL1F3ZW4zLVRUUy0xMkh6LTEuN0ItQmFzZSAtLWxvY2FsX2RpciAiX19NT0RFTF9ESVJfXyIK"
set "MSG_CONDA_MISSING_B64=44CQ5o+Q56S644CRIOacquaJvuWIsCBDb25kYSDlkb3ku6TvvIzot7Pov4cgdmlkZW9saW5nbyDnjq/looPmv4DmtLvjgIIK44CQ5o+Q56S644CRIOWwhue7p+e7reS9v+eUqOW9k+WJjSBQeXRob24g546v5aKD5ZCv5Yqo44CCCg=="
set "MSG_CONDA_ENV_MISSING_B64=44CQ5o+Q56S644CRIOacquaJvuWIsCBDb25kYSDnjq/looPvvJpfX0VOVl9OQU1FX18K44CQ5o+Q56S644CRIOWwhue7p+e7reS9v+eUqOW9k+WJjSBQeXRob24g546v5aKD5ZCv5Yqo44CCCg=="

if not exist "%TTS_MODEL_FILE%" (
    call :print_message "%MSG_TTS_B64%"
)

call :activate_conda

if /i "%~1"=="--no-app" exit /b 0
if /i "%ONEKEYSTART_NO_APP%"=="1" exit /b 0

python app.py
pause
exit /b 0

:activate_conda
set "CONDA_BAT="
for /f "delims=" %%I in ('where conda.bat 2^>nul') do (
    set "CONDA_BAT=%%~fI"
    goto :activate_conda_found
)
call :print_message "%MSG_CONDA_MISSING_B64%"
goto :eof

:activate_conda_found
call "%CONDA_BAT%" activate "%CONDA_ENV_NAME%" >nul 2>nul
if errorlevel 1 (
    call :print_message "%MSG_CONDA_ENV_MISSING_B64%"
)
goto :eof

:print_message
powershell -NoProfile -ExecutionPolicy Bypass -Command "$msg=[System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('%~1')); $msg=$msg.Replace('__MODEL_FILE__',$env:TTS_MODEL_FILE).Replace('__MODEL_DIR__',$env:TTS_MODEL_DIR).Replace('__ENV_NAME__',$env:CONDA_ENV_NAME); [Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Write-Host $msg"
goto :eof
