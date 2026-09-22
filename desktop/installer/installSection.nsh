; initMultiUser and elevation can re-apply /D after customInit. Lock the final
; target immediately before the upstream section computes executable/link paths.
!define EXCELMANUS_INSTALL_LOCATION_LOCK_ACTIVE
${If} $ExcelManusExistingPath != ""
  StrCpy $INSTDIR $ExcelManusExistingPath
${EndIf}
; Validate the new payload's destinations before removing the old app. A newly
; introduced path must not copy files through a pre-existing workspace junction.
InitPluginsDir
File /oname=$PLUGINSDIR\excelmanus-new-files.txt "${PROJECT_DIR}\.build\excelmanus-installed-files.txt"
StrCpy $R9 "$PLUGINSDIR\excelmanus-new-files.txt"
Call ExcelManusValidateOwnedFiles
!include "${PROJECT_DIR}\node_modules\app-builder-lib\templates\nsis\installSection.nsh"
