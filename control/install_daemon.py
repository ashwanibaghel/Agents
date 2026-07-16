import os
import sys
import subprocess

def main():
    project_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    venv_python = os.path.join(project_dir, ".venv", "Scripts", "python.exe")
    main_py = os.path.join(project_dir, "main.py")
    
    if not os.path.exists(venv_python):
        print(f"ERROR: Virtual environment python not found at {venv_python}")
        sys.exit(1)
        
    vbs_path = os.path.join(project_dir, "start_worker_invisibly.vbs")
    
    # 1. Create the invisible launcher VBS script
    vbs_content = f'''Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd.exe /c \"{venv_python}\" \"{main_py}\"", 0, false
'''
    with open(vbs_path, "w", encoding="utf-8") as f:
        f.write(vbs_content)
    print(f"Created invisible launcher script at: {vbs_path}")
    
    # 2. Get Windows Startup folder
    appdata = os.environ.get("APPDATA")
    if not appdata:
        print("ERROR: APPDATA environment variable not found.")
        sys.exit(1)
        
    startup_dir = os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
    shortcut_path = os.path.join(startup_dir, "AshwaniAgentCompanyWorker.lnk")
    
    # 3. Create shortcut using PowerShell (pure OS utility, no pip packages required)
    ps_command = f'''
    $WshShell = New-Object -ComObject WScript.Shell;
    $Shortcut = $WshShell.CreateShortcut("{shortcut_path}");
    $Shortcut.TargetPath = "wscript.exe";
    $Shortcut.Arguments = "`"{vbs_path}`"";
    $Shortcut.WorkingDirectory = "{project_dir}";
    $Shortcut.Description = "Starts the Ashwani Agent Company local polling worker invisibly at login.";
    $Shortcut.Save();
    '''
    
    try:
        subprocess.run(["powershell", "-Command", ps_command], check=True, capture_output=True)
        print(f"Success! Startup shortcut created at: {shortcut_path}")
        print("The worker will now run completely in the background automatically whenever you log in.")
        print("To start it manually right now in the background, run: wscript.exe start_worker_invisibly.vbs")
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Failed to create shortcut via PowerShell: {e.stderr.decode('utf-8', errors='ignore')}")
        sys.exit(1)

if __name__ == "__main__":
    main()
