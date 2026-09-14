Set fso = CreateObject("Scripting.FileSystemObject")
Set WshShell = CreateObject("WScript.Shell")

targetBat = ""
If fso.FileExists("D:\jarvis_project\start_lila_companion.bat") Then
    targetBat = "D:\jarvis_project\start_lila_companion.bat"
ElseIf fso.FileExists("C:\Users\Rishabh_Joshi\Downloads\jarvis_project\start_lila_companion.bat") Then
    targetBat = "C:\Users\Rishabh_Joshi\Downloads\jarvis_project\start_lila_companion.bat"
End If

If targetBat <> "" Then
    WshShell.Run "cmd.exe /c """ & targetBat & """ ana", 0, False
End If
