import os
import sys
import shutil
import yaml
import subprocess
import codecs

# Force UTF-8 output on Windows to avoid UnicodeEncodeError for emojis
sys.stdout = codecs.getwriter("utf-8")(sys.stdout.detach())
sys.stderr = codecs.getwriter("utf-8")(sys.stderr.detach())

def main():
    print("==================================================")
    print("STARTING LOCAL E2E GIT LIFECYCLE DEMONSTRATION")
    print("==================================================")

    # 1. Back up supabase config
    cfg_path = "config/supabase.yaml"
    bak_path = "config/supabase.yaml.bak"
    shutil.copyfile(cfg_path, bak_path)
    print("Backed up config/supabase.yaml")

    try:
        # 2. Disable supabase
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        cfg["enabled"] = False
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f)
        print("Disabled Supabase task source in config/supabase.yaml")

        # 3. Clean local tasks directories
        for folder in ["inbox", "working", "done", "blocked"]:
            dir_path = os.path.join("tasks", folder)
            if os.path.exists(dir_path):
                for file in os.listdir(dir_path):
                    if file.endswith((".yaml", ".yml")):
                        os.remove(os.path.join(dir_path, file))
        print("Cleared tasks/ directories")

        # 4. Create the YAML feature task
        task_data = {
            "task_id": "OI-V31-E2E-LOCAL",
            "project": "oi_labs",
            "task_type": "feature",
            "objective": "Add developer comment to Sidebar footer",
            "context": "Open frontend/src/components/Sidebar.tsx and add the comment <!-- V3.1 E2E Git Lifecycle Test --> inside the file to prove the git lifecycle automation.",
            "acceptance_criteria": [
                "frontend/src/components/Sidebar.tsx contains the comment <!-- V3.1 E2E Git Lifecycle Test -->"
            ],
            "constraints": [
                "Only modify frontend/src/components/Sidebar.tsx"
            ],
            "validation_commands": [
                "git status --short"
            ],
            "autonomy_level": 2,
            "status": "inbox"
        }
        os.makedirs("tasks/inbox", exist_ok=True)
        with open("tasks/inbox/OI-V31-E2E-LOCAL.yaml", "w", encoding="utf-8") as f:
            yaml.dump(task_data, f)
        print("Created feature task tasks/inbox/OI-V31-E2E-LOCAL.yaml")

        # Clean receipt file if exists
        receipt_path = "state/receipts/OI-V31-E2E-LOCAL.json"
        if os.path.exists(receipt_path):
            os.remove(receipt_path)

        # 5. Run the worker and redirect stdout/stderr to local_e2e.log
        print("\n[Worker] Starting Antigravity Worker in single-run mode...")
        log_path = "local_e2e.log"
        with open(log_path, "w", encoding="utf-8") as log_f:
            proc = subprocess.run([sys.executable, "-u", "main.py", "--once"], stdout=log_f, stderr=subprocess.STDOUT)
            
        print(f"\n[Worker] Finished with exit code: {proc.returncode}")
        
        # Read and print the log
        if os.path.exists(log_path):
            print("\n" + "=" * 40 + " WORKER LOG OUTPUT " + "=" * 40)
            with open(log_path, "r", encoding="utf-8") as log_f:
                print(log_f.read())
            print("=" * 99)

    finally:
        # 6. Restore supabase config
        if os.path.exists(bak_path):
            shutil.copyfile(bak_path, cfg_path)
            os.remove(bak_path)
            print("Restored config/supabase.yaml")

if __name__ == "__main__":
    main()
