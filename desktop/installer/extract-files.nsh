!define EXCELMANUS_FAST_EXTRACT_ACTIVE

!macro extractUsing7za FILE
  Var /GLOBAL ExcelManusStage
  Var /GLOBAL ExcelManusDestination
  Var /GLOBAL ExcelManusFind
  Var /GLOBAL ExcelManusEntry

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
  !ifmacrodef ExcelManusVerifyExtractedPayload
    !insertmacro ExcelManusVerifyExtractedPayload
  !endif
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
    Push "$ExcelManusStage\$ExcelManusEntry"
    Push "$ExcelManusDestination\$ExcelManusEntry"
    Call ExcelManusMoveTree
  ExcelManusMoveSkip:
    FindNext $ExcelManusFind $ExcelManusEntry
    Goto ExcelManusMoveNext

  ExcelManusMoveDone:
    FindClose $ExcelManusFind
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
