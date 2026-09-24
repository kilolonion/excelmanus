; Stack: source, destination. Move whole subtrees whenever possible. If an
; upgrade preserves user files inside resources, merge only the shared parents
; instead of copying the entire extracted payload a second time.
!ifndef EXCELMANUS_MOVE_FUNCTIONS
!define EXCELMANUS_MOVE_FUNCTIONS
Function ExcelManusMoveTree
  Exch $1
  Exch
  Exch $0
  Push $2
  Push $3
  Push $4
  Push $5

  System::Call 'kernel32::GetFileAttributesW(w r0) i.r2'
  System::Call 'kernel32::GetFileAttributesW(w r1) i.r3'
  ; Never traverse a junction, including a target added after manifest checks.
  IntOp $4 $2 & 0x400
  IntCmp $4 0 +2
    Goto failed
  ${If} $3 <> -1
    IntOp $4 $3 & 0x400
    IntCmp $4 0 +2
      Goto failed
    IntOp $4 $2 & 0x10
    IntOp $5 $3 & 0x10
    IntCmp $4 $5 0 failed failed
    ; Both directories exist: renaming cannot merge them, so do not waste the
    ; scanner retry delay on an operation that cannot succeed.
    IntCmp $4 0 copyFile mergeDirectory mergeDirectory
  ${EndIf}

  StrCpy $4 0
  retryMove:
    ClearErrors
    Rename "$0" "$1"
    IfErrors 0 done
    IntOp $4 $4 + 1
    ${If} $4 < 8
      Sleep 250
      Goto retryMove
    ${EndIf}
    IntOp $4 $2 & 0x10
    IntCmp $4 0 copyFile mergeDirectory mergeDirectory

  mergeDirectory:
    ClearErrors
    CreateDirectory "$1"
    IfErrors failed
    FindFirst $2 $3 "$0\*"
    IfErrors failed
  nextEntry:
    StrCmp $3 "" directoryDone
    StrCmp $3 "." skipEntry
    StrCmp $3 ".." skipEntry
    Push "$0\$3"
    Push "$1\$3"
    Call ExcelManusMoveTree
  skipEntry:
    FindNext $2 $3
    Goto nextEntry
  directoryDone:
    FindClose $2
    ClearErrors
    RMDir "$0"
    IfErrors failed
    Goto done

  copyFile:
    !ifmacrodef ExcelManusRecordFileCopy
      !insertmacro ExcelManusRecordFileCopy
    !endif
    ; Only a colliding or persistently locked file takes the Shell copy path.
    StrCpy $4 0
  retryCopy:
    ClearErrors
    CopyFiles /SILENT "$0" "$1"
    IfErrors 0 copied
    IntOp $4 $4 + 1
    ${If} $4 < 5
      Sleep 1000
      Goto retryCopy
    ${EndIf}
    MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "$(appCannotBeClosed)" /SD IDCANCEL IDRETRY retryCopy
    Goto failed
  copied:
    ClearErrors
    Delete "$0"
    IfErrors failed
    Goto done

  failed:
    DetailPrint "$(^ErrorWriting)$1"
    SetErrorLevel 2
    Abort
  done:
    Pop $5
    Pop $4
    Pop $3
    Pop $2
    Pop $0
    Pop $1
    ClearErrors
FunctionEnd
!endif
