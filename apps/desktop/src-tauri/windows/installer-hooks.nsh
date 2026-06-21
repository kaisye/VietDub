!macro NSIS_HOOK_PREINSTALL
  ; Older VietDub builds can leave the frozen FastAPI sidecar alive when the
  ; updater exits. Stop the process tree before NSIS replaces installed files.
  nsExec::ExecToStack '"$SYSDIR\taskkill.exe" /F /T /IM "videodubbing-api.exe"'
  Pop $0
  Pop $1

  ; The updater normally exits the app before this hook runs. This handles the
  ; remaining race without /T, which would also terminate this installer.
  nsExec::ExecToStack '"$SYSDIR\taskkill.exe" /F /IM "VietDub.exe"'
  Pop $0
  Pop $1
  Sleep 750
!macroend
