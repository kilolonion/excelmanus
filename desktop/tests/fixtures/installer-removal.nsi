Unicode true
RequestExecutionLevel user
SilentInstall silent
Name "ExcelManus file removal fixture"
OutFile "${TEST_EXE}"
!include LogicLib.nsh
!include "${PROJECT_DIR}\installer\safe-remove.nsh"
!insertmacro ExcelManusRemovalFunctions ""
Section
  StrCpy $INSTDIR "${TEST_INSTALL_DIR}"
  StrCpy $R9 "${TEST_MANIFEST}"
  Call ExcelManusRemoveOwnedFiles
SectionEnd
