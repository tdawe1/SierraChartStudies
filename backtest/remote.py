"""Ship a backtest job to a remote box over SSH and pull results back.

The job bundle is self-contained: the backtest/ source, the data CSV,
and params.json. The remote only needs `python3` -- no pip, no build.

Local and remote runs are the same command inside the bundle::

    python3 bt.py run --data JOB.csv --params params.json --out out/

`bt.py remote` wraps: tar -> scp -> ssh run -> scp back. Use --dry-run
to inspect the exact commands before anything touches the network.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tarfile
import tempfile
import uuid

def build_bundle(src_dir: str, data_path: str, params_path: str,
                 dest_tar: str, job_name: str = "job") -> str:
    with tarfile.open(dest_tar, "w:gz") as tar:
        for fn in sorted(os.listdir(src_dir)):
            if fn.startswith("__pycache__"):
                continue
            tar.add(os.path.join(src_dir, fn), arcname=f"{job_name}/backtest/{fn}")
        tar.add(data_path, arcname=f"{job_name}/data.csv")
        tar.add(params_path, arcname=f"{job_name}/params.json")
    return dest_tar


def run_remote(host: str, bundle: str, remote_dir: str = "/tmp/bt",
               job_name: str = "job", dry_run: bool = False,
               extra_args: str = "", notify: str | None = None) -> tuple:
    """Copy bundle, run it, copy results back.
    Returns (commands used, local output dir).

    Each call gets a unique remote workspace (mode 700) and local
    output dir, so concurrent runs never share paths."""
    run_id = uuid.uuid4().hex[:8]
    workdir = f"{remote_dir}/bt-{run_id}"
    remote_tar = f"{workdir}.tgz"
    local_out = f"./out-{run_id}"
    cmds = [
        f"ssh {shlex.quote(host)} {shlex.quote(f'mkdir -p -m 700 {workdir}')}",
        f"scp {shlex.quote(bundle)} {shlex.quote(host + ':' + remote_tar)}",
        f"ssh {shlex.quote(host)} {shlex.quote(
            f'cd {workdir} && tar xzf {remote_tar} '
            f'&& cd {job_name} && python3 backtest/bt.py run '
            f'--data data.csv --params params.json --out out {extra_args}')}",
        f"scp -r {shlex.quote(host + ':' + workdir + '/' + job_name + '/out')} "
        f"{shlex.quote(local_out)}",
    ]
    if notify:
        cmds.append(f"email {notify} < {local_out}/report.txt"
                    + (" (dry-run, not sent)" if dry_run else ""))
    if dry_run:
        return cmds, local_out
    try:
        for c in cmds[:4]:
            subprocess.run(c, shell=True, check=True)
    finally:
        # Best-effort: never leave remote workspaces behind on failure.
        try:
            subprocess.run(
                f"ssh {shlex.quote(host)} "
                f"{shlex.quote(f'rm -rf {workdir} {remote_tar}')}",
                shell=True, check=False, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass
    return cmds, local_out
