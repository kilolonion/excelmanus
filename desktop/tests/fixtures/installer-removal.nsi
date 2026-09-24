Unicode true
RequestExecutionLevel user
SilentInstall silent
Name "ExcelManus file removal fixture"
OutFile "${TEST_EXE}"
!include LogicLib.nsh
!include "${PROJECT_DIR}\installer\safe-remove.nsh"
!insertmacro ExcelManusRemovalFunctions ""
!insertmacro ExcelManusVerificationFunctions
Section
  StrCpy $INSTDIR "${TEST_INSTALL_DIR}"
  StrCpy $R9 "${TEST_MANIFEST}"
  ${GetParameters} $0
  ${If} $0 == "/verify"
    Call ExcelManusVerifyOwnedFiles
    SetErrorLevel 0
    Quit
  ${EndIf}
  Call ExcelManusRemoveOwnedFiles
SectionEnd
