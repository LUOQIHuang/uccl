import os
import signal
import subprocess
import sys
from pathlib import Path

stage = Path(__file__).resolve().parent
run = Path(sys.argv[1])

def run_check(command, timeout):
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
    return result.returncode, result.stdout

def gpu_guard(label):
    rc, output = run_check(["mx-smi"], 12)
    (run / f"gpu_{label}.txt").write_text(output)
    if rc or "no process found" not in output or "Available" not in output:
        print(f"STOP_GPU_NOT_IDLE stage={label} rc={rc}\n{output}", flush=True)
        return False
    rc, output = run_check(["journalctl", "-k", "-b", "--no-pager", "-q", "-g", "kill queue timeout|destroy ce ringbuf failed|results in a decrease in the number of available ringbufs"], 15)
    if rc not in (0, 1) or output.strip():
        print(f"STOP_GPU_QUEUE_ERRORS stage={label} rc={rc}\n{output}", flush=True)
        return False
    return True

for name in ("control", "exact", "group"):
    if not gpu_guard(name):
        sys.exit(81)
    path = run / f"{name}.log"
    print(f"STAGE_BEGIN={name}", flush=True)
    with path.open("w") as log:
        proc = subprocess.Popen([sys.executable, "-u", str(stage / "probe.py"), name], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            rc = proc.wait(timeout=40)
        except subprocess.TimeoutExpired:
            try:
                result = subprocess.run(["gdb", "-q", "-batch", "-p", str(proc.pid), "-ex", "set pagination off", "-ex", "thread 1", "-ex", "bt", "-ex", "detach"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=12)
                (run / f"{name}_stack.log").write_text(result.stdout)
            except (subprocess.TimeoutExpired, OSError) as exc:
                (run / f"{name}_stack.log").write_text(str(exc))
            try:
                rc = proc.wait(timeout=35)
            except subprocess.TimeoutExpired:
                print(f"STAGE_TIMEOUT={name} PID={proc.pid}", flush=True)
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    rc = proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    try:
                        rc = proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        print(f"STOP_PROCESS_STILL_PRESENT PID={proc.pid}", flush=True)
                        sys.exit(82)
    output = path.read_text()
    print(output, flush=True)
    print(f"STAGE_RC={name}:{rc}", flush=True)
    if rc or f"STAGE_OK={name}" not in output:
        stack = run / f"{name}_stack.log"
        if stack.exists():
            print(stack.read_text(), flush=True)
        print(f"STOP_FIRST_FAILED_STAGE={name}", flush=True)
        sys.exit(83)
    if not gpu_guard(f"{name}_after"):
        sys.exit(84)
print("SINGLE_NODE_BUFFER_MATRIX_OK", flush=True)
