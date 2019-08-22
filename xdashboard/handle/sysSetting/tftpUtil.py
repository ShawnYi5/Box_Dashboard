# 读取文件--修改参数--写入文件

import re


class TFTPConfigFile(object):
    def __init__(self, filepath):
        self.filepath = filepath

    def disableIsNo(self):
        with open(self.filepath, 'r') as fin:
            fileStr = fin.read()
        fileStr = re.sub('disable\s*=\s*yes', 'disable=no', fileStr)
        with open(self.filepath, 'w') as fout:
            fout.write(fileStr)

    def disableIsYes(self):
        with open(self.filepath, 'r') as fin:
            fileStr = fin.read()
        fileStr = re.sub('disable\s*=\s*no', 'disable=yes', fileStr)
        with open(self.filepath, 'w+') as fout:
            fout.write(fileStr)
