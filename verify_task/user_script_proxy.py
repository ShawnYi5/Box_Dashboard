import os
import sys
import zipfile
import subprocess
import json


def execute_cmd(cmd, timeout=120, **kwargs):
    with subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, **kwargs) as p:
        stdout, stderr = p.communicate(timeout=timeout)
    return p.returncode, stdout, stderr


def unzip(path):
    f = zipfile.ZipFile(os.path.join(os.getcwd(), path))
    for file in f.namelist():
        f.extract(file, os.getcwd())
    f.close()


if __name__ == "__main__":
    argv = sys.argv[1:]
    file_path = argv[0]
    unzip(file_path)
    python_path = os.path.join(os.getcwd(), 'python', 'python.exe')
    cmd = '"{}" main.py'.format(python_path)
    _, stdout, _ = execute_cmd(cmd)
    print(stdout)
