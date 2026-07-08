# ============================================================
#  Первичная настройка распознавания визиток: Ollama + vision-модель.
#  Запуск:
#     powershell -ExecutionPolicy Bypass -File ollama_setup\install_and_pull.ps1
#
#  По умолчанию используется ОБЛАЧНАЯ модель Ollama (qwen3-vl:235b-cloud):
#  она выполняется на серверах Ollama, читает визитки точно и быстро,
#  но требует разового входа в аккаунт (ollama signin) у каждого пользователя.
#
#  Хотите офлайн без входа? Замените $Model на локальную, например
#  "qwen2.5vl:3b" (точная, но медленная) или "moondream" (лёгкая, слабая),
#  и так же поменяйте local.vision.model в config.yaml.
# ============================================================

$ErrorActionPreference = "Stop"
[System.Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Модель должна совпадать с config: local.vision.model
$Model = "qwen3-vl:235b-cloud"

function Test-Ollama {
    return ($null -ne (Get-Command ollama -ErrorAction SilentlyContinue))
}

# --- [1/4] Установка Ollama ---
Write-Host "[1/4] Проверяю наличие Ollama..."
if (-not (Test-Ollama)) {
    Write-Host "      Ollama не найдена. Устанавливаю через winget..."
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($null -ne $winget) {
        winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements
    } else {
        Write-Warning "winget не найден. Скачайте установщик вручную: https://ollama.com/download/windows"
        Write-Warning "После установки запустите этот скрипт снова."
        Read-Host "Нажмите Enter, чтобы закрыть"
        exit 1
    }
    # Обновим PATH в текущей сессии, чтобы найти только что установленный бинарь.
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path", "User")
    if (-not (Test-Ollama)) {
        Write-Warning "Ollama установлена, но не видна в PATH. Перезапустите компьютер и запустите скрипт снова."
        Read-Host "Нажмите Enter, чтобы закрыть"
        exit 1
    }
}

# --- [2/4] Запуск службы Ollama ---
Write-Host "[2/4] Запускаю Ollama в фоне (если ещё не запущена)..."
if ($null -eq (Get-Process -Name "ollama" -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 3
}

# Облачная ли модель? (тег оканчивается на -cloud)
$IsCloud = $Model -match "-cloud$"

# --- [3/4] Вход в аккаунт Ollama (только для облачных моделей) ---
if ($IsCloud) {
    Write-Host ""
    Write-Host "[3/4] Облачная модель '$Model' требует входа в аккаунт Ollama (разово)."
    Write-Host "      Сейчас откроется браузер для входа. Войдите или создайте"
    Write-Host "      бесплатный аккаунт на ollama.com, затем вернитесь в это окно."
    Write-Host ""
    try {
        ollama signin
    } catch {
        Write-Warning "Не удалось запустить вход: $($_.Exception.Message)"
        Write-Warning "Выполните вручную в терминале:  ollama signin  — затем запустите скрипт снова."
        Read-Host "Нажмите Enter, чтобы закрыть"
        exit 1
    }
} else {
    Write-Host "[3/4] Локальная модель — вход не требуется."
}

# --- [4/4] Загрузка модели ---
Write-Host "[4/4] Загружаю модель '$Model'..."
ollama pull $Model

# Для облачной модели проверяем, что вход реально выполнен (иначе будет 401).
if ($IsCloud) {
    Write-Host "      Проверяю, что облако отвечает..."
    try {
        $body = @{ model = $Model; prompt = "Reply with one word: OK"; stream = $false } | ConvertTo-Json
        $resp = Invoke-RestMethod -Uri "http://localhost:11434/api/generate" `
                    -Method Post -Body $body -ContentType "application/json" -TimeoutSec 60
        Write-Host "      Ответ модели: $($resp.response)"
    } catch {
        Write-Warning "Облако ответило ошибкой (вероятно, вход не завершён): $($_.Exception.Message)"
        Write-Warning "Выполните 'ollama signin' и запустите этот скрипт снова."
        Read-Host "Нажмите Enter, чтобы закрыть"
        exit 1
    }
}

Write-Host ""
Write-Host "[ГОТОВО] Модель '$Model' доступна на http://localhost:11434"
Write-Host "         В приложении уже выбран движок «Локально — vision-модель (Ollama)»."
Read-Host "Нажмите Enter, чтобы закрыть"
