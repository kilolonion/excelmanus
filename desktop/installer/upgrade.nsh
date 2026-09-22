!include nsDialogs.nsh
!include "${PROJECT_DIR}\installer\safe-remove.nsh"

!ifdef BUILD_UNINSTALLER
  !insertmacro ExcelManusRemovalFunctions "un."

  !macro customUnInit
    ; Even explicit legacy flags must never erase a profile (it may contain
    ; the default workspace). This product supports program-only uninstall.
    ${GetParameters} $0
    ClearErrors
    ${GetOptions} $0 "--delete-app-data" $1
    ${IfNot} ${Errors}
      SetErrorLevel 2
      Abort "ExcelManus uninstall preserves settings, conversations and user files."
    ${EndIf}
  !macroend

  !macro customRemoveFiles
    InitPluginsDir
    File /oname=$PLUGINSDIR\excelmanus-fallback-files.txt "${PROJECT_DIR}\.build\excelmanus-installed-files.txt"
    StrCpy $R9 "$INSTDIR\${EXCELMANUS_MANIFEST}"
    ${IfNot} ${FileExists} "$R9"
      ; Pre-manifest versions: remove only names in the new signed package.
      ; Unknown legacy files remain rather than risking user-created files.
      StrCpy $R9 "$PLUGINSDIR\excelmanus-fallback-files.txt"
    ${EndIf}
    Call un.ExcelManusRemoveOwnedFiles
    Delete "$INSTDIR\${EXCELMANUS_MANIFEST}"
    Delete "$INSTDIR\${UNINSTALL_FILENAME}"
    SetOutPath $TEMP
    RMDir "$INSTDIR"
  !macroend
!else
  Var ExcelManusInstallChoice
  Var ExcelManusExistingPath
  Var ExcelManusMigrateRadio
  Var ExcelManusReinstallRadio

  !macro customInit
    StrCpy $ExcelManusInstallChoice "migrate"
    ${GetParameters} $0
    ClearErrors
    ${GetOptions} $0 "/excelmanus-mode=" $1
    ${IfNot} ${Errors}
      ${If} $1 != "migrate"
      ${AndIf} $1 != "reinstall"
        SetErrorLevel 2
        Abort "Use /excelmanus-mode=migrate or /excelmanus-mode=reinstall"
      ${EndIf}
      StrCpy $ExcelManusInstallChoice $1
    ${EndIf}
    ClearErrors
    ${GetOptions} $0 "--delete-app-data" $1
    ${IfNot} ${Errors}
      SetErrorLevel 2
      Abort "ExcelManus installation always preserves user data."
    ${EndIf}
    ; Prefer the existing machine installation. The upstream installer removes
    ; the current user's duplicate as part of an all-user install as well.
    ReadRegStr $ExcelManusExistingPath HKLM "${INSTALL_REGISTRY_KEY}" InstallLocation
    ${If} $ExcelManusExistingPath != ""
      StrCpy $hasPerMachineInstallation "1"
      StrCpy $hasPerUserInstallation "0"
      StrCpy $installMode "all"
      SetShellVarContext all
    ${Else}
      ReadRegStr $ExcelManusExistingPath HKCU "${INSTALL_REGISTRY_KEY}" InstallLocation
      ${If} $ExcelManusExistingPath != ""
        StrCpy $hasPerMachineInstallation "0"
        StrCpy $hasPerUserInstallation "1"
        StrCpy $installMode "CurrentUser"
        SetShellVarContext current
      ${EndIf}
    ${EndIf}
    ${If} $ExcelManusExistingPath != ""
      StrCpy $INSTDIR $ExcelManusExistingPath
    ${EndIf}
  !macroend

  !macro customInstallMode
    ; Do not let switching user/machine scope create a second installation.
    ReadRegStr $0 HKLM "${INSTALL_REGISTRY_KEY}" InstallLocation
    ${If} $0 != ""
      StrCpy $isForceMachineInstall "1"
    ${Else}
      ReadRegStr $0 HKCU "${INSTALL_REGISTRY_KEY}" InstallLocation
      ${If} $0 != ""
        StrCpy $isForceCurrentInstall "1"
      ${EndIf}
    ${EndIf}
  !macroend

  !macro ExcelManusUpgradePage
    Page custom ExcelManusUpgradePageShow ExcelManusUpgradePageLeave
  !macroend

  !macro ExcelManusUpgradeFunctions
  !insertmacro ExcelManusRemovalFunctions ""
  Function ExcelManusUpgradePageShow
    ${If} $ExcelManusExistingPath == ""
      Abort
    ${EndIf}
    ; A changed directory must never leave an old launchable copy behind.
    StrCpy $INSTDIR $ExcelManusExistingPath
    !insertmacro MUI_HEADER_TEXT "更新 ExcelManus" "请选择安装方式。两种方式都保留设置、会话和用户文件。"
    nsDialogs::Create 1018
    Pop $0
    ${If} $0 == error
      Abort
    ${EndIf}
    ${NSD_CreateLabel} 0 0 100% 24u "将替换已有 ExcelManus：$\r$\n$ExcelManusExistingPath"
    Pop $0
    ${NSD_CreateRadioButton} 0 29u 100% 12u "迁移数据安装（推荐）"
    Pop $ExcelManusMigrateRadio
    ${NSD_CreateLabel} 12u 44u 95% 22u "替换旧程序，沿用设置、会话和快捷方式。数据保留原位，工作区无需搬迁。"
    Pop $0
    ${NSD_CreateRadioButton} 0 70u 100% 12u "卸载后安装"
    Pop $ExcelManusReinstallRadio
    ${NSD_CreateLabel} 12u 85u 95% 22u "移除旧程序后重新安装，重新创建快捷方式。设置和会话同样保留。"
    Pop $0
    ${NSD_CreateLabel} 0 112u 100% 28u "仅处理 ExcelManus 程序文件。不会删除、移动或修改工作区、表格、文档及其他用户文件。安装完成后只保留一个已注册版本。"
    Pop $0
    ${If} $ExcelManusInstallChoice == "reinstall"
      ${NSD_Check} $ExcelManusReinstallRadio
    ${Else}
      ${NSD_Check} $ExcelManusMigrateRadio
    ${EndIf}
    nsDialogs::Show
  FunctionEnd

  Function ExcelManusUpgradePageLeave
    ${NSD_GetState} $ExcelManusReinstallRadio $0
    ${If} $0 == ${BST_CHECKED}
      StrCpy $ExcelManusInstallChoice "reinstall"
    ${Else}
      StrCpy $ExcelManusInstallChoice "migrate"
    ${EndIf}
  FunctionEnd
  !macroend
!endif
