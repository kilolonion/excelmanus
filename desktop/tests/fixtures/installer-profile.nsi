Unicode true
RequestExecutionLevel user
SilentInstall silent
SetCompress off
Name "ExcelManus installer performance fixture"
OutFile "${BENCH_EXE}"
!addplugindir /x86-unicode "${BENCH_PLUGINS}"
!include LogicLib.nsh
!ifdef BENCH_FAST
  !include "${PROJECT_DIR}\installer\extract-files.nsh"
  LangString appCannotBeClosed 1033 "Cannot install fixture files"
  !macro ExcelManusRecordExtractStage
    !insertmacro RecordStage "extract-files"
  !macroend
!endif
Var BenchLog
Var BenchPrevious
Var BenchNow
Var BenchElapsed

!macro RecordStage NAME
  System::Call 'kernel32::GetTickCount() i .s'
  Pop $BenchNow
  IntOp $BenchElapsed $BenchNow - $BenchPrevious
  FileWrite $BenchLog '{"stage":"${NAME}","ms":$BenchElapsed}$\r$\n'
  ; Flush the file so the parent can report each completed stage immediately.
  FileClose $BenchLog
  FileOpen $BenchLog "${BENCH_RESULT}" a
  FileSeek $BenchLog 0 END
  StrCpy $BenchPrevious $BenchNow
!macroend

Section
  InitPluginsDir
  FileOpen $BenchLog "${BENCH_RESULT}" w
  System::Call 'kernel32::GetTickCount() i .s'
  Pop $BenchPrevious
  File /oname=$PLUGINSDIR\payload.7z "${BENCH_ARCHIVE}"
  !insertmacro RecordStage "unpack-embedded-archive"
  StrCpy $INSTDIR "${BENCH_ROOT}\installed"
  !ifdef BENCH_FAST
    SetOutPath $INSTDIR
    !insertmacro extractUsing7za "$PLUGINSDIR\payload.7z"
    !insertmacro RecordStage "move-files"
  !else
  SetOutPath "$PLUGINSDIR\7z-out"
  Nsis7z::Extract "$PLUGINSDIR\payload.7z"
  !insertmacro RecordStage "extract-files"
  SetOutPath $INSTDIR
  ClearErrors
  CopyFiles /SILENT "$PLUGINSDIR\7z-out\*" $INSTDIR
  IfErrors failed
  !insertmacro RecordStage "copy-files"
  !endif
  ClearErrors
  CopyFiles /SILENT "$EXEPATH" "${BENCH_ROOT}\cached-installer.exe"
  IfErrors failed
  !insertmacro RecordStage "cache-installer"
  ; These are only the fixture's own staging and destination directories.
  RMDir /r "$PLUGINSDIR\7z-out"
  !insertmacro RecordStage "cleanup-staging"
  SetOutPath "${BENCH_ROOT}"
  RMDir /r $INSTDIR
  !insertmacro RecordStage "remove-installed-files"
  FileClose $BenchLog
  SetErrorLevel 0
  Quit
  failed:
    FileClose $BenchLog
    SetErrorLevel 2
    Quit
SectionEnd
