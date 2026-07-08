# Собирает debug-APK CardScan-приложения (Capacitor WebView-обёртка).
# Запускать из ОБЫЧНОГО PowerShell/Terminal (не из песочницы агента) —
# см. заметку про "Unable to establish loopback connection" в README.md.
#
# Результат: android\app\build\outputs\apk\debug\app-debug.apk

$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$env:JAVA_HOME = "C:\Program Files\Eclipse Adoptium\jdk-17.0.19.10-hotspot"
$env:ANDROID_HOME = "$root\android-sdk"
$env:Path = "$env:JAVA_HOME\bin;$env:ANDROID_HOME\platform-tools;$env:Path"

Write-Output "JAVA_HOME = $env:JAVA_HOME"
Write-Output "ANDROID_HOME = $env:ANDROID_HOME"

if (-not (Test-Path "$root\node_modules")) {
    Write-Output "Устанавливаю npm-зависимости..."
    Push-Location $root
    npm install
    Pop-Location
}

if (-not (Test-Path "$root\android")) {
    Write-Output "Добавляю Android-платформу (npx cap add android)..."
    Push-Location $root
    npx cap add android
    Pop-Location
}

Write-Output "Синхронизирую веб-содержимое (npx cap sync android)..."
Push-Location $root
npx cap sync android
Pop-Location

Write-Output "Собираю debug APK (gradlew assembleDebug)..."
Push-Location "$root\android"
.\gradlew.bat assembleDebug
Pop-Location

$apk = "$root\android\app\build\outputs\apk\debug\app-debug.apk"
if (Test-Path $apk) {
    $size = [math]::Round((Get-Item $apk).Length / 1MB, 1)
    Write-Output ""
    Write-Output "Готово: $apk ($size MB)"
    Write-Output "Скопируйте файл на телефон и установите (нужно разрешить 'установку из неизвестных источников')."
} else {
    Write-Output "APK не найден по ожидаемому пути — смотрите вывод gradlew выше на предмет ошибок."
}
