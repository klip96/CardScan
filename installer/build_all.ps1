# ============================================================
#  Сборка инсталлятора «Сканер визиток» В ОДНО ДЕЙСТВИЕ
#  с АВТО-УСТАНОВКОЙ недостающих компонентов.
#
#  Запускается из build_all.bat (двойной клик). Делает:
#    [1] проверяет/ставит Python (winget -> иначе скачивает офиц. установщик)
#    [2] проверяет/ставит Inno Setup 6 (аналогично)
#    [3] создаёт .venv и ставит зависимости + PyInstaller
#    [4] собирает CardScan.exe (PyInstaller)
#    [5] компилирует инсталлятор (Inno Setup) -> installer\Output\
#
#  Если что-то нужно доустановить — попросит права администратора (UAC).
# ============================================================

$ErrorActionPreference = 'Stop'
[System.Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# --- Версии для запасного скачивания (можно обновлять) ---
$PY_URL   = 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe'
$INNO_URL = 'https://jrsoftware.org/download.php/is.exe'   # стабильная ссылка на последнюю версию (редирект)

function Test-Cmd($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Update-PathFromRegistry {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user    = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = (@($machine, $user) | Where-Object { $_ }) -join ';'
}

# ---------- Поиск Python (>=3.9) ----------
function Find-PythonExe {
    $cands = New-Object System.Collections.Generic.List[string]
    $g = Get-Command python -ErrorAction SilentlyContinue
    if ($g) { $cands.Add($g.Source) }
    if (Test-Cmd py) {
        try {
            $exe = & py -3 -c "import sys;print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe) { $cands.Add($exe.Trim()) }
        } catch {}
    }
    $cands.Add("$env:ProgramFiles\Python312\python.exe")
    $cands.Add("$env:ProgramFiles\Python311\python.exe")
    $cands.Add("$env:LocalAppData\Programs\Python\Python312\python.exe")
    $cands.Add("$env:LocalAppData\Programs\Python\Python311\python.exe")
    foreach ($c in $cands) {
        if ($c -and (Test-Path $c)) {
            try {
                & $c -c "import sys;exit(0 if sys.version_info[:2]>=(3,9) else 1)" 2>$null
                if ($LASTEXITCODE -eq 0) { return $c }
            } catch {}
        }
    }
    return $null
}

# ---------- Поиск компилятора Inno Setup ----------
function Find-ISCC {
    $cands = @(
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:LocalAppData\Programs\Inno Setup 6\ISCC.exe"   # winget ставит сюда (per-user)
    )
    $g = Get-Command iscc -ErrorAction SilentlyContinue
    if ($g) { $cands += $g.Source }
    foreach ($c in $cands) { if ($c -and (Test-Path $c)) { return $c } }
    return $null
}

function Ensure-Python {
    $py = Find-PythonExe
    if ($py) { Write-Host "  Python найден: $py"; return $py }

    Write-Host "  Python не найден — устанавливаю..."
    if (Test-Cmd winget) {
        Write-Host "  ...через winget"
        try {
            winget install -e --id Python.Python.3.12 --silent `
                --accept-package-agreements --accept-source-agreements --scope machine
        } catch { Write-Host "  winget не справился, пробую прямое скачивание..." }
        Update-PathFromRegistry
        $py = Find-PythonExe
    }
    if (-not $py) {
        $tmp = Join-Path $env:TEMP 'python-installer.exe'
        Write-Host "  ...скачиваю $PY_URL"
        Invoke-WebRequest -Uri $PY_URL -OutFile $tmp
        Write-Host "  ...устанавливаю Python (тихо)"
        Start-Process -FilePath $tmp -Wait -ArgumentList `
            '/quiet', 'InstallAllUsers=1', 'PrependPath=1', 'Include_pip=1'
        Update-PathFromRegistry
        $py = Find-PythonExe
    }
    if (-not $py) {
        throw "Не удалось установить Python автоматически. Поставьте вручную: https://www.python.org/downloads/"
    }
    Write-Host "  Python установлен: $py"
    return $py
}

function Ensure-ISCC {
    $iscc = Find-ISCC
    if ($iscc) { Write-Host "  Inno Setup найден: $iscc"; return $iscc }

    Write-Host "  Inno Setup не найден — устанавливаю..."
    if (Test-Cmd winget) {
        Write-Host "  ...через winget"
        try {
            winget install -e --id JRSoftware.InnoSetup --silent `
                --accept-package-agreements --accept-source-agreements
        } catch { Write-Host "  winget не справился, пробую прямое скачивание..." }
        $iscc = Find-ISCC
    }
    if (-not $iscc) {
        $tmp = Join-Path $env:TEMP 'innosetup-installer.exe'
        Write-Host "  ...скачиваю $INNO_URL"
        Invoke-WebRequest -Uri $INNO_URL -OutFile $tmp
        Write-Host "  ...устанавливаю Inno Setup (тихо)"
        Start-Process -FilePath $tmp -Wait -ArgumentList `
            '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-'
        $iscc = Find-ISCC
    }
    if (-not $iscc) {
        throw "Не удалось установить Inno Setup автоматически. Поставьте вручную: https://jrsoftware.org/isdl.php"
    }
    Write-Host "  Inno Setup установлен: $iscc"
    return $iscc
}

# ============================================================
#  Если чего-то не хватает и мы не админ — перезапускаемся с правами админа
#  (нужно только для установки компонентов).
# ============================================================
$needInstall = (-not (Find-PythonExe)) -or (-not (Find-ISCC))
if ($needInstall -and -not (Test-IsAdmin)) {
    Write-Host "Нужно доустановить компоненты — запрашиваю права администратора..."
    try {
        Start-Process powershell.exe -Verb RunAs -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
    } catch {
        Write-Host "[ОШИБКА] Не удалось получить права администратора. Запустите build_all.bat от имени администратора." -ForegroundColor Red
        Read-Host "Нажмите Enter, чтобы закрыть"
    }
    exit
}

# ============================================================
#  Основной процесс сборки
# ============================================================
try {
    Set-Location (Split-Path -Parent $PSScriptRoot)
    Write-Host "============================================================"
    Write-Host "  Сборка инсталлятора: Сканер визиток (CardScan)"
    Write-Host "============================================================"

    Write-Host "`n[1/5] Проверка/установка Python..."
    $py = Ensure-Python

    Write-Host "`n[2/5] Проверка/установка Inno Setup..."
    $iscc = Ensure-ISCC

    Write-Host "`n[3/5] Окружение и зависимости (может занять несколько минут)..."
    if (-not (Test-Path ".venv\Scripts\python.exe")) {
        & $py -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "Не удалось создать виртуальное окружение .venv" }
    }
    $venvPy = (Resolve-Path ".venv\Scripts\python.exe").Path
    & $venvPy -m pip install --upgrade pip
    & $venvPy -m pip install -r requirements.txt pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "Не удалось установить зависимости (requirements.txt / pyinstaller)" }

    Write-Host "`n[4/5] Сборка exe через PyInstaller..."
    & $venvPy -m PyInstaller --noconfirm installer\build_exe.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller завершился с ошибкой (см. вывод выше)" }
    if (-not (Test-Path "dist\CardScan\CardScan.exe")) {
        throw "Файл dist\CardScan\CardScan.exe не найден после сборки"
    }
    Write-Host "  Готово: dist\CardScan\CardScan.exe"

    Write-Host "`n[5/5] Компиляция инсталлятора через Inno Setup..."
    & $iscc "installer\installer.iss"
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup завершился с ошибкой (см. вывод выше)" }

    Write-Host "`n============================================================"
    Write-Host "  ГОТОВО!"
    Write-Host "  Готовый инсталлятор лежит в:  installer\Output\"
    Write-Host "  (имя вида CardScan-setup-0.1.0.exe)"
    Write-Host "============================================================"
}
catch {
    Write-Host "`n[ОШИБКА] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Если не получается — соберите вручную по инструкции в README.md"
}
finally {
    Read-Host "`nНажмите Enter, чтобы закрыть это окно"
}
