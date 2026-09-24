; NSIS counts script instructions, while archive plugins report extraction
; progress. Neither measures the whole installation (including upgrades and
; installer caching). Keep their controls hidden until the install has finished
; and use a separate native activity indicator that they cannot reset.
!ifdef APP_GUID
  !include "${PROJECT_DIR}\installer\upgrade.nsh"
!endif
!ifndef BUILD_UNINSTALLER
  !ifdef APP_GUID
    !macro ExcelManusVerifyExtractedPayload
      ; Validate the extracted tree before moving it into place. Read the copy
      ; embedded directly by installSection, not a possibly truncated archive.
      Push $INSTDIR
      StrCpy $INSTDIR $ExcelManusStage
      StrCpy $R9 "$PLUGINSDIR\excelmanus-new-files.txt"
      Call ExcelManusVerifyOwnedFiles
      Pop $INSTDIR
    !macroend
  !endif
  ; Keep upstream pages available when selecting our extraction wrapper later.
  !addincludedir "${PROJECT_DIR}\node_modules\app-builder-lib\templates\nsis"
  Var ExcelManusProgress
  Var ExcelManusStatus

  ; This hook runs after the directory page, immediately before MUI_PAGE_INSTFILES.
  ; Setting SHOW earlier would attach it to the directory/install-mode page.
  !macro customPageAfterChangeDir
    !ifmacrodef ExcelManusUpgradePage
      !insertmacro ExcelManusUpgradePage
    !endif
    !define MUI_PAGE_CUSTOMFUNCTION_SHOW ExcelManusProgressShow
    !define MUI_PAGE_CUSTOMFUNCTION_LEAVE ExcelManusProgressLeave
  !macroend

  ; Copy the existing control's rectangle and font to respect DPI and language.
  ; The original handles/IDs must stay intact for NSIS and its archive plugin.
  !macro ExcelManusCreateControl ORIGINAL CLASS STYLE ID RESULT
    System::Alloc 16
    Pop $0
    System::Call 'user32::GetWindowRect(p ${ORIGINAL}, p r0)'
    System::Call 'user32::MapWindowPoints(p 0, p $mui.InstFilesPage, p r0, i 2)'
    System::Call '*$0(i .r2, i .r3, i .r4, i .r5)'
    System::Free $0
    IntOp $4 $4 - $2
    IntOp $5 $5 - $3
    System::Call 'user32::CreateWindowExW(i 0, w "${CLASS}", w "", i ${STYLE}, i r2, i r3, i r4, i r5, p $mui.InstFilesPage, p ${ID}, p 0, p 0) p.s'
    Pop ${RESULT}
    SendMessage ${ORIGINAL} ${WM_GETFONT} 0 0 $0
    SendMessage ${RESULT} ${WM_SETFONT} $0 1
  !macroend

  !macro customHeader
    !include "${PROJECT_DIR}\installer\move-files.nsh"
    !ifmacrodef ExcelManusUpgradeFunctions
      !insertmacro ExcelManusUpgradeFunctions
      !insertmacro ExcelManusVerificationFunctions
    !endif
    ; Change include resolution only after upstream pages/multiUser.nsh have
    ; loaded. Doing this in the initial header selects NSIS's unrelated
    ; MultiUser.nsh on Windows, where filenames are case-insensitive.
    !cd "${PROJECT_DIR}/installer"
    Function ExcelManusProgressShow
      Push $0
      Push $2
      Push $3
      Push $4
      Push $5
      ; WS_CHILD | WS_VISIBLE | PBS_MARQUEE. Never send PBM_SETPOS to this
      ; control: Windows does not support that message with PBS_MARQUEE.
      !insertmacro ExcelManusCreateControl $mui.InstFilesPage.ProgressBar "msctls_progress32" 0x50000008 11004 $ExcelManusProgress
      !insertmacro ExcelManusCreateControl $mui.InstFilesPage.Text "STATIC" 0x50000000 11006 $ExcelManusStatus
      ${If} $ExcelManusProgress != 0
      ${AndIf} $ExcelManusStatus != 0
        SendMessage $ExcelManusStatus ${WM_SETTEXT} 0 "STR:$(MUI_TEXT_INSTALLING_SUBTITLE)"
        ShowWindow $mui.InstFilesPage.ProgressBar ${SW_HIDE}
        ShowWindow $mui.InstFilesPage.Text ${SW_HIDE}
        ; PBM_SETMARQUEE: animate on the UI thread even while installation waits
        ; for disk writes, antivirus scanning, or the previous uninstaller.
        SendMessage $ExcelManusProgress 0x040A 1 30
      ${Else}
        ; If a control cannot be created, retain the standard install UI.
        Call ExcelManusProgressLeave
      ${EndIf}
      Pop $5
      Pop $4
      Pop $3
      Pop $2
      Pop $0
    FunctionEnd

    Function ExcelManusProgressLeave
      ; MUI calls LEAVE after installation succeeds or aborts. Restore its real
      ; completion/error display; never report success from a timer or estimate.
      ${If} $ExcelManusProgress != 0
        SendMessage $ExcelManusProgress 0x040A 0 0
        System::Call 'user32::DestroyWindow(p $ExcelManusProgress)'
        StrCpy $ExcelManusProgress 0
      ${EndIf}
      ${If} $ExcelManusStatus != 0
        System::Call 'user32::DestroyWindow(p $ExcelManusStatus)'
        StrCpy $ExcelManusStatus 0
      ${EndIf}
      ShowWindow $mui.InstFilesPage.ProgressBar ${SW_SHOW}
      ShowWindow $mui.InstFilesPage.Text ${SW_SHOW}
    FunctionEnd
  !macroend

  !macro customInstall
    !ifdef APP_GUID
      !ifndef EXCELMANUS_NO_INSTALLER_CACHE_ACTIVE
        !error "ExcelManus installer cache override was not loaded"
      !endif
      !ifndef EXCELMANUS_SAFE_UPGRADE_ACTIVE
        !error "ExcelManus safe upgrade override was not loaded"
      !endif
      !ifndef EXCELMANUS_INSTALL_LOCATION_LOCK_ACTIVE
        !error "ExcelManus install location guard was not loaded"
      !endif
    !endif
    !ifndef EXCELMANUS_FAST_EXTRACT_ACTIVE
      !error "ExcelManus extraction override was not loaded; check NSIS include order"
    !endif
  !macroend
!endif
