!define EXCELMANUS_FAST_EXTRACT_ACTIVE

!macro extractUsing7za FILE
  Var /GLOBAL ExcelManusStage
  Var /GLOBAL ExcelManusDestination
  Var /GLOBAL ExcelManusFind
  Var /GLOBAL ExcelManusEntry
  Var /GLOBAL ExcelManusCopyAttempt
  Var /GLOBAL ExcelManusMoveAttempt

  StrCpy $ExcelManusDestination $OUTDIR
  ; Stage on the destination volume, with its inherited permissions. Moving a
  ; tree from $PLUGINSDIR would fail across drives and retain temp-directory ACLs.
  ClearErrors
  GetTempFileName $ExcelManusStage $ExcelManusDestination
  IfErrors ExcelManusExtractFailed
  Delete $ExcelManusStage
  CreateDirectory $ExcelManusStage
  IfErrors ExcelManusExtractFailed
  SetOutPath $ExcelManusStage
  Nsis7z::Extract "${FILE}"
  !ifmacrodef ExcelManusRecordExtractStage
    !insertmacro ExcelManusRecordExtractStage
  !endif
  SetOutPath $ExcelManusDestination

  ; Rename each top-level entry: the resources directory contains tens of
  ; thousands of files, but moving it on the same volume is one operation.
  FindFirst $ExcelManusFind $ExcelManusEntry "$ExcelManusStage\*"
  ExcelManusMoveNext:
    StrCmp $ExcelManusEntry "" ExcelManusMoveDone
    StrCmp $ExcelManusEntry "." ExcelManusMoveSkip
    StrCmp $ExcelManusEntry ".." ExcelManusMoveSkip
    StrCpy $ExcelManusMoveAttempt 0
  ExcelManusMoveRetry:
    ClearErrors
    Rename "$ExcelManusStage\$ExcelManusEntry" "$ExcelManusDestination\$ExcelManusEntry"
    IfErrors 0 ExcelManusMoveSkip
    ; A scanner can briefly retain a just-written child. Retry the cheap move
    ; before falling back to copying the entire remaining tree.
    IntOp $ExcelManusMoveAttempt $ExcelManusMoveAttempt + 1
    ${If} $ExcelManusMoveAttempt < 8
      Sleep 250
      Goto ExcelManusMoveRetry
    ${EndIf}
    Goto ExcelManusCopyRemaining
  ExcelManusMoveSkip:
    FindNext $ExcelManusFind $ExcelManusEntry
    Goto ExcelManusMoveNext

  ExcelManusCopyRemaining:
    FindClose $ExcelManusFind
    ; A nonempty existing target or a file lock can prevent a rename. Preserve
    ; existing unrelated files and use the original copy behavior for leftovers.
    StrCpy $ExcelManusCopyAttempt 0
  ExcelManusCopyRetry:
    ClearErrors
    CopyFiles /SILENT "$ExcelManusStage\*" $ExcelManusDestination
    IfErrors 0 ExcelManusExtractDone
    IntOp $ExcelManusCopyAttempt $ExcelManusCopyAttempt + 1
    ${If} $ExcelManusCopyAttempt < 5
      Sleep 1000
      Goto ExcelManusCopyRetry
    ${EndIf}
    MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "$(appCannotBeClosed)" /SD IDCANCEL IDRETRY ExcelManusCopyRetry
    Goto ExcelManusExtractFailed

  ExcelManusMoveDone:
    FindClose $ExcelManusFind
  ExcelManusExtractDone:
    ; This path was created by GetTempFileName under the destination; never
    ; recursively remove the destination itself or an existing user directory.
    RMDir /r $ExcelManusStage
    ClearErrors
    Goto ExcelManusExtractEnd

  ExcelManusExtractFailed:
    SetOutPath $ExcelManusDestination
    DetailPrint "$(^ErrorWriting)$ExcelManusDestination"
    SetErrorLevel 2
    Abort
  ExcelManusExtractEnd:
!macroend
