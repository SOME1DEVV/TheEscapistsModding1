@echo off
REM ============================================================
REM  Guard Key Names mod - установщик для The Escapists 1 (PC)
REM  Перетащите TheEscapists.exe на этот файл или запустите:
REM     INSTALL.bat "C:\...\The Escapists\TheEscapists.exe"
REM  Нужен Python 3 в PATH (https://python.org, галка Add to PATH)
REM ============================================================
setlocal
cd /d "%~dp0"

set "EXE=%~1"
if "%EXE%"=="" (
    if exist TheEscapists.exe (set "EXE=TheEscapists.exe") else (
        echo.
        echo  Перетащите TheEscapists.exe на INSTALL.bat или укажите путь аргументом.
        echo.
        pause
        exit /b 1
    )
)
if not exist "%EXE%" (
    echo  Файл не найден: %EXE%
    pause
    exit /b 1
)
if not exist "%EXE%.bak" (
    copy /y "%EXE%" "%EXE%.bak" >nul
    echo  Сделан бэкап: %EXE%.bak
)

REM --- выбираем Python ---
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
    echo.
    echo  Python 3 не найден в PATH. Установи его: https://python.org
    echo  ^(при установке отметь галку "Add python.exe to PATH"^)
    echo.
    pause
    exit /b 1
)

echo.
%PY% "%~dp0te1_guardkeys.py" check "%EXE%"
if errorlevel 1 goto :fail
echo.
%PY% "%~dp0te1_guardkeys.py" patch "%EXE%" -o "%~dpn1_guardkeys%~x1"
if errorlevel 4 goto :already
if errorlevel 1 goto :fail

echo.
echo  ============================================================
echo   Готово! Теперь:
echo     1^) закрой игру
echo     2^) замени   %EXE%
echo        на        %~dpn1_guardkeys%~x1
echo        ^(старую версию не удаляй - это твой откат, если что^)
echo   Откат: верни %EXE%.bak или Steam -^> "Проверить целостность файлов"
echo  ============================================================
echo.
pause
exit /b 0

:already
echo.
echo  Мод уже установлен в этом exe - ничего делать не нужно.
pause
exit /b 0

:fail
echo.
echo  Что-то пошло не так (см. сообщение выше).
pause
exit /b 1
