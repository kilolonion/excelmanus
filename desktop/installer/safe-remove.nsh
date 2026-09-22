; Delete only files recorded as application-owned. Never recurse over the
; installation directory, profile or workspaces. Shared by native fixtures.
!include FileFunc.nsh
!include TextFunc.nsh
!define EXCELMANUS_MANIFEST "excelmanus-installed-files.txt"

!macro ExcelManusRemovalFunctions PREFIX
Function ${PREFIX}ExcelManusCanonicalPath
  ; NSIS GetFullPathName can fail when intermediate directories do not exist.
  ; Win32 canonicalization works before a first install has created the tree.
  Push $0
  Push $1
  System::Call 'kernel32::GetFullPathNameW(w "$R2", i ${NSIS_MAX_STRLEN}, w .r0, p 0) i.r1'
  ${If} $1 = 0
  ${OrIf} $1 >= ${NSIS_MAX_STRLEN}
    SetErrorLevel 2
    Abort "Invalid or excessively long ExcelManus installation path."
  ${EndIf}
  StrCpy $R2 $0
  Pop $1
  Pop $0
FunctionEnd

Function ${PREFIX}ExcelManusValidateOwnedPath
  ; $R2 is an absolute path inside $INSTDIR. Validate containment and every
  ; existing ancestor (including the root) before any file is removed.
  StrLen $R4 "$INSTDIR\"
  StrCpy $R5 $R2 $R4
  StrCmp $R5 "$INSTDIR\" 0 invalid
  StrCpy $R3 $R2
  loop:
    System::Call 'kernel32::GetFileAttributesW(w "$R3") i.s'
    Pop $R4
    ; INVALID_FILE_ATTRIBUTES can be rendered as unsigned 4294967295. Use an
    ; integer comparison, so a new (not-yet-created) destination remains valid.
    ${If} $R4 <> -1
      IntOp $R4 $R4 & 0x400
      ${If} $R4 != 0
        Goto invalid
      ${EndIf}
    ${EndIf}
    ${GetParent} $R3 $R4
    StrCmp $R3 $R4 valid
    StrCmp $R4 "" valid
    StrCpy $R3 $R4
    Goto loop
  invalid:
    SetErrorLevel 2
    Abort "Cannot safely remove application files: $R2"
  valid:
FunctionEnd

Function ${PREFIX}ExcelManusRemoveOwnedFiles
  ; $R9 contains the manifest to use. Complete a validation pass before delete.
  Call ${PREFIX}ExcelManusValidateOwnedFiles
  StrCpy $R8 1
  Call ${PREFIX}ExcelManusReadOwnedFiles
FunctionEnd

Function ${PREFIX}ExcelManusValidateOwnedFiles
  StrCpy $R8 0
  Call ${PREFIX}ExcelManusReadOwnedFiles
FunctionEnd

Function ${PREFIX}ExcelManusReadOwnedFiles
    StrCpy $R2 $INSTDIR
    Call ${PREFIX}ExcelManusCanonicalPath
    StrCpy $INSTDIR $R2
    ClearErrors
    FileOpen $R0 "$R9" r
    IfErrors invalidManifest
    FileReadWord $R0 $R1
    StrCmp $R1 65279 0 invalidManifest
  next:
    ClearErrors
    FileReadUTF16LE $R0 $R1
    IfErrors done
    ${TrimNewLines} $R1 $R1
    StrCmp $R1 "" next
    ; Reject absolute paths, wildcards, alternate data streams and traversal.
    StrCpy $R3 $R1 1
    StrCmp $R3 "\" invalidManifest
    StrCpy $R3 0
  character:
    StrCpy $R4 $R1 1 $R3
    StrCmp $R4 "" checked
    StrCmp $R4 ":" invalidManifest
    StrCmp $R4 "*" invalidManifest
    StrCmp $R4 "?" invalidManifest
    StrCmp $R4 "/" invalidManifest
    StrCpy $R4 $R1 2 $R3
    StrCmp $R4 ".." invalidManifest
    IntOp $R3 $R3 + 1
    Goto character
  checked:
    StrCpy $R2 "$INSTDIR\$R1"
    Call ${PREFIX}ExcelManusCanonicalPath
    Call ${PREFIX}ExcelManusValidateOwnedPath
    ${If} $R8 == 1
      ClearErrors
      IfFileExists "$R2" 0 next
      Delete "$R2"
      IfErrors busy
      ; Prune empty directories only; unrelated files keep their parents alive.
      ${GetParent} $R2 $R3
      prune:
        StrCmp $R3 $INSTDIR next
        ClearErrors
        RMDir "$R3"
        ; A nonempty/locked directory also makes every ancestor nonempty.
        ; Stop here instead of retrying the entire path for every sibling file.
        ; Later deletions retry this directory when it can actually be empty.
        IfErrors next
        ${GetParent} $R3 $R4
        StrCmp $R3 $R4 next
        StrCpy $R3 $R4
        Goto prune
    ${EndIf}
    Goto next
  done:
    FileClose $R0
    Return
  busy:
    FileClose $R0
    SetErrorLevel 2
    Abort "Close ExcelManus and retry. Cannot remove: $R2"
  invalidManifest:
    FileClose $R0
    SetErrorLevel 2
    Abort "Invalid ExcelManus application file manifest. No recursive removal is allowed."
FunctionEnd
!macroend
