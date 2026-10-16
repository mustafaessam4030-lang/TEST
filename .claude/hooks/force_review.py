import json, sys, subprocess
data = json.load(sys.stdin)
if data.get("stop_hook_active"):
    sys.exit(0)
diff = subprocess.run(["git", "diff", "--stat"], capture_output=True, text=True).stdout
if diff.strip():
    print("Code changed. Run the reviewer subagent, fix all BLOCKERs, then finish.", file=sys.stderr)
    sys.exit(2)
sys.exit(0)
