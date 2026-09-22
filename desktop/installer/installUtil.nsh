; Keep electron-builder's registration and error handling. Never invoke an old
; recursive uninstaller: use this package's signed, file-list-only uninstaller
; for upgrades of legacy releases too.
!include "${PROJECT_DIR}\node_modules\app-builder-lib\templates\nsis\include\installUtil.nsh"
; The upstream function deliberately remains uncalled after replacing its
; public macro. NSIS strips it; suppress only the unreferenced-function warning.
!pragma warning disable 6010
!define EXCELMANUS_SAFE_UPGRADE_ACTIVE
!macroundef setIsTryToKeepShortcuts
!macro setIsTryToKeepShortcuts
  StrCpy $isTryToKeepShortcuts "false"
  ${If} $ExcelManusInstallChoice == "migrate"
    StrCpy $isTryToKeepShortcuts "true"
  ${EndIf}
!macroend

!macroundef uninstallOldVersion
!macro uninstallOldVersion ROOT_KEY
  !insertmacro readReg $installationDir "${ROOT_KEY}" "${INSTALL_REGISTRY_KEY}" InstallLocation
  ${If} $installationDir != ""
    InitPluginsDir
    File /oname=$PLUGINSDIR\excelmanus-safe-uninstall.exe "${UNINSTALLER_OUT_FILE}"
    StrCpy $0 "/currentuser"
    ${If} $installMode == "all"
    ${AndIf} "${ROOT_KEY}" != "HKEY_CURRENT_USER"
      StrCpy $0 "/allusers"
    ${EndIf}
    ${If} $ExcelManusInstallChoice == "migrate"
      StrCpy $0 "$0 --keep-shortcuts"
    ${EndIf}
    ClearErrors
    ExecWait '"$PLUGINSDIR\excelmanus-safe-uninstall.exe" /S $0 --updated _?=$installationDir' $R0
    ${If} ${Errors}
      SetErrorLevel 2
      Abort "Cannot remove the previous ExcelManus version. Installation stopped."
    ${EndIf}
    ${If} $R0 != 0
      SetErrorLevel 2
      Abort "Previous ExcelManus removal failed. Installation stopped."
    ${EndIf}
  ${EndIf}
  ClearErrors
  StrCpy $R0 0
!macroend
