; Keep electron-builder's registry, shortcuts, updater and signed-uninstaller
; logic. Override only extraction after its macros have been loaded.
!cd "${PROJECT_DIR}/node_modules/app-builder-lib/templates/nsis"
!include "${PROJECT_DIR}\node_modules\app-builder-lib\templates\nsis\include\installer.nsh"
!macroundef extractUsing7za
!include "${PROJECT_DIR}\installer\extract-files.nsh"
