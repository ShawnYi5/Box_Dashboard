import hashlib
import os
import shutil
import time

OneMegaBytes = 1024**2

def GetFileMd5(filename):
    if not os.path.isfile(filename):
        return 'none'
    myhash = hashlib.md5()
    f = open(filename, 'rb')
    while True:
        b = f.read(OneMegaBytes)
        if not b:
            break
        myhash.update(b)
    f.close()
    return myhash.hexdigest()


def move_tmp_file(tmppath):
    script_path = '/home/mnt/stop_start_web_script'
    (filepath, tempfilename) = os.path.split(tmppath)
    ext = os.path.splitext(tempfilename)[1].lower()
    if ext == '.gz':
        ext = 'tar.gz'
    filemd5 = GetFileMd5(tmppath)
    new_script_path = os.path.join(script_path, '{}_{}{}'.format(time.time(), filemd5, ext))
    shutil.move(tmppath, new_script_path)
    return new_script_path


def current_file_directory():
    return os.path.dirname(os.path.abspath(__file__))


def touch_file(file_path):
    basedir = os.path.dirname(file_path)
    if not os.path.exists(basedir):
        os.makedirs(basedir)
    open(file_path, 'w').close()


def delete_file_safely(file_path):
    try:
        os.remove(file_path)
    except Exception:
        pass
