Unicode true
ManifestSupportedOS all
RequestExecutionLevel user
Name "ExcelManus installer progress test"
OutFile "${TEST_EXE}"
AutoCloseWindow true
ShowInstDetails nevershow

!include MUI2.nsh
!include FileFunc.nsh
!addincludedir "${NSIS_TEMPLATES}\include"
!include "${PROGRESS_INCLUDE}"
!addplugindir /x86-unicode "${TEST_PLUGINS}"
!define PRODUCT_NAME "ExcelManus installer progress test"
Var packageArch

!insertmacro customPageAfterChangeDir
; Exercise the production callbacks without displaying a test window.
!define /redef MUI_PAGE_CUSTOMFUNCTION_SHOW TestProgressShow
!define /redef MUI_PAGE_CUSTOMFUNCTION_LEAVE TestProgressLeave
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "TradChinese"
LangString appCannotBeClosed ${LANG_ENGLISH} "Test extraction failed"
LangString appCannotBeClosed ${LANG_SIMPCHINESE} "Test extraction failed"
LangString appCannotBeClosed ${LANG_TRADCHINESE} "Test extraction failed"
!insertmacro customHeader
!include installer.nsh
!insertmacro customInstall

Var TestMode

!macro AssertEqual ACTUAL EXPECTED CODE
  ${If} "${ACTUAL}" != "${EXPECTED}"
    SetErrorLevel ${CODE}
    Quit
  ${EndIf}
!macroend

Function .onInit
  ${GetParameters} $0
  StrCpy $LANGUAGE $0 4
  StrCpy $TestMode $0 "" 5
FunctionEnd

Function TestProgressShow
  ShowWindow $HWNDPARENT ${SW_HIDE}
  Call ExcelManusProgressShow
FunctionEnd

Section
  InitPluginsDir
  File /oname=$PLUGINSDIR\app-64.7z "${TEST_PAYLOAD}"
  StrCpy $packageArch "64"
  StrCpy $INSTDIR "${TEST_INSTALL_DIR}"
  SetOutPath $INSTDIR
  ; Exercise the builder integration and the destination-local extraction path.
  !insertmacro decompress
  ${If} ${Silent}
    !insertmacro AssertEqual $ExcelManusProgress "" 10
    !insertmacro AssertEqual $ExcelManusStatus "" 11
    FileOpen $0 "${TEST_RESULT}" w
    FileWrite $0 "silent"
    FileClose $0
    SetErrorLevel 0
    Quit
  ${EndIf}

  ; The default bar still exists for the engine/plugin, but is not visible.
  GetDlgItem $0 $mui.InstFilesPage 1004
  !insertmacro AssertEqual $0 $mui.InstFilesPage.ProgressBar 12
  System::Call 'user32::GetWindowLongW(p r0, i -16) i.r1'
  IntOp $1 $1 & 0x10000000
  !insertmacro AssertEqual $1 0 13
  GetDlgItem $0 $mui.InstFilesPage 11004
  !insertmacro AssertEqual $0 $ExcelManusProgress 14
  System::Call 'user32::GetWindowLongW(p r0, i -16) i.r1'
  IntOp $1 $1 & 0x10000008
  !insertmacro AssertEqual $1 268435464 15

  ; Reproduce the archive and engine disagreeing about both range and position.
  SendMessage $mui.InstFilesPage.ProgressBar 0x0406 0 100
  SendMessage $mui.InstFilesPage.ProgressBar 0x0402 70 0
  SendMessage $mui.InstFilesPage.ProgressBar 0x0406 0 30000
  SendMessage $mui.InstFilesPage.ProgressBar 0x0402 1 0
  SendMessage $mui.InstFilesPage.Text ${WM_SETTEXT} 0 "STR:70%"
  System::Call 'user32::GetWindowLongW(p $ExcelManusProgress, i -16) i.r1'
  IntOp $1 $1 & 0x10000008
  !insertmacro AssertEqual $1 268435464 16
  System::Call 'user32::GetWindowTextW(p $ExcelManusStatus, w .r1, i ${NSIS_MAX_STRLEN})'
  !insertmacro AssertEqual $1 "$(MUI_TEXT_INSTALLING_SUBTITLE)" 17

  ${If} $TestMode == "abort"
    Abort
  ${EndIf}
SectionEnd

Function TestProgressLeave
  Call ExcelManusProgressLeave
  !insertmacro AssertEqual $ExcelManusProgress 0 18
  !insertmacro AssertEqual $ExcelManusStatus 0 19
  System::Call 'user32::GetWindowLongW(p $mui.InstFilesPage.ProgressBar, i -16) i.r1'
  IntOp $1 $1 & 0x10000000
  !insertmacro AssertEqual $1 268435456 20
  System::Call 'user32::GetWindowLongW(p $mui.InstFilesPage.Text, i -16) i.r1'
  IntOp $1 $1 & 0x10000000
  !insertmacro AssertEqual $1 268435456 21
  StrCpy $1 "success"
  IfAbort 0 +2
    StrCpy $1 "abort"
  !insertmacro AssertEqual $1 $TestMode 22
  FileOpen $0 "${TEST_RESULT}" w
  FileWrite $0 $1
  FileClose $0
  SetErrorLevel 0
  ${If} $TestMode == "abort"
    SetErrorLevel 2
  ${EndIf}
  Quit
FunctionEnd
