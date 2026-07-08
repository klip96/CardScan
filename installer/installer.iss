; ============================================================
;  Inno Setup script — заготовка инсталлятора для Windows.
;  Собирает дистрибутив из готового exe (см. installer/build_exe.spec).
;
;  Как использовать:
;    1) Сначала соберите exe:  pyinstaller installer\build_exe.spec
;       (результат окажется в dist\CardScan\ или dist\CardScan.exe)
;    2) Проверьте плейсхолдеры ниже (помечены <<< ... >>>).
;    3) Откройте этот файл в Inno Setup Compiler и нажмите Compile,
;       либо: "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\installer.iss
;
;  Документация Inno Setup: https://jrsoftware.org/ishelp/
; ============================================================

#define MyAppName "Сканер визиток"
#define MyAppNameLatin "CardScan"
#define MyAppVersion "0.1.0"                 ; <<< ОБНОВИТЕ версию при релизе
#define MyAppPublisher "CardScan"
#define MyAppExeName "CardScan.exe"          ; <<< имя exe из PyInstaller

[Setup]
; AppId — фиксированный GUID приложения. НЕ меняйте его между версиями,
; иначе обновления будут ставиться как отдельная программа.
AppId={{8F3A2C7E-9B4D-4E1A-A6C2-CA4D5CA40001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppNameLatin}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Готовый инсталлятор кладётся в installer\Output\
OutputDir=Output
OutputBaseFilename={#MyAppNameLatin}-setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; Установка в Program Files требует прав администратора
PrivilegesRequired=admin

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительно:"; Flags: unchecked

[Files]
; <<< ПУТИ ниже зависят от режима сборки PyInstaller.
; Вариант A (onedir, рекомендуется): копируем всю папку dist\CardScan\*
Source: "..\dist\{#MyAppNameLatin}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Вариант B (onefile): вместо строки выше используйте одну строку:
; Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

; Шаблон конфигурации и скрипты установки локальной модели
Source: "..\config.example.yaml"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\ollama_setup\*"; DestDir: "{app}\ollama_setup"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; --- Первичная настройка локальной vision-модели (по желанию пользователя) ---
; Запускаем PowerShell-скрипт установки Ollama + загрузки модели.
; nowait + skipifsilent: не блокируем установку, можно пропустить.
Filename: "powershell.exe"; \
    Parameters: "-ExecutionPolicy Bypass -File ""{app}\ollama_setup\install_and_pull.ps1"""; \
    Description: "Настроить распознавание: Ollama + вход + модель qwen3-vl"; \
    Flags: postinstall skipifsilent runascurrentuser unchecked

; --- Запуск приложения после установки ---
Filename: "{app}\{#MyAppExeName}"; \
    Description: "Запустить {#MyAppName}"; \
    Flags: nowait postinstall skipifsilent

[Code]
// Плейсхолдер для произвольной логики первого запуска.
// Например, можно скопировать config.example.yaml в config.yaml,
// если config.yaml ещё не существует, при завершении установки.
procedure CurStepChanged(CurStep: TSetupStep);
var
  CfgPath, TplPath: string;
begin
  if CurStep = ssPostInstall then
  begin
    CfgPath := ExpandConstant('{app}\config.yaml');
    TplPath := ExpandConstant('{app}\config.example.yaml');
    if (not FileExists(CfgPath)) and FileExists(TplPath) then
      FileCopy(TplPath, CfgPath, False);
  end;
end;
