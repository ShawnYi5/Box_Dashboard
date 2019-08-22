import os
import argparse
import psutil
import json
from box_dashboard import xlogging
from apiv1.runCmpr import bitmap
from box_dashboard import xsys, boxService
from itertools import groupby
from operator import itemgetter

_logger = xlogging.getLogger(__name__)

block_size = align_size = 64 * 1024  # Bytes
sector_size = 512
compact_cmd = '/sbin/aio/compresspp'
cache = {'map2-path': 'bit-map'}

_arith = boxService.get_compression_algorithm()
_rank = boxService.get_compression_rank()
_pio = boxService.get_compression_measure_busy_value()


def print_logs(msgs):
    print(msgs)


# 位图最大长度(nbit)，允许最大地址值
def get_bitmap_max_len(qcow_path):
    max_offset_val = os.path.getsize(qcow_path)
    position_max = max_offset_val // align_size
    return position_max


# map文件行: 0x100000:0x280000:128
# 转换为: [块1地址, 块2地址, ...]  (地址缩放1/64K) (块大小:64K)
def convert_map_line_into_positions(map_line):
    line_params = map_line.split(':')
    byte_offset = int(line_params[1], base=16)
    total_bytes = int(line_params[2], base=10) * sector_size
    if byte_offset & 0xFFFF != 0:
        _logger.warning(r'map file byte_offset is not 64k align, map_line: {0}'.format(map_line))
        return []
    block_nums = total_bytes >> 16  # 块数
    pos0 = byte_offset >> 16  # 起始块地址
    return range(pos0, pos0 + block_nums)


# map文件行，生成位图
def set_map_line_positions_to_bitmap(_bitmap, line):
    positions = convert_map_line_into_positions(line)
    for pos in positions:
        _bitmap.set(pos)


# map文件生成位图，若map_path=None，则返回None
def generate_map_file_bitmap(map_path, qcow_path):
    if map_path is None:
        return None

    if map_path in cache:
        return cache[map_path]

    cache.clear()
    _bitmap = bitmap.BitMap(maxnum=get_bitmap_max_len(qcow_path))
    with open(map_path, 'rt') as fin:
        for map_line in fin:
            set_map_line_positions_to_bitmap(_bitmap, map_line)
    cache[map_path] = _bitmap
    return _bitmap


# 子map文件某行，对应多个块地址；在父map_bitmap中搜索，得到子map特有的块地址(缩放后的)
# 若父map_bitmap=None，则返回子所有的地址块
def get_map1_line_particular_positions(map1_line, map2_bitmap):
    line_positions = convert_map_line_into_positions(map1_line)
    if map2_bitmap is None:
        return line_positions
    priv_pos = []
    for pos in line_positions:
        if not map2_bitmap.test(pos):
            priv_pos.append(pos)
    return priv_pos


# 文件检验，qcow不存在抛异常，child_map、parent_map不存在赋值None
def verify_map_qcow(child_map, parent_map, qcow_file):
    if qcow_file and not os.path.exists(qcow_file):
        raise Exception(qcow_file + ' not exist')
    if child_map and not os.path.exists(child_map):
        child_map = None
    if parent_map and not os.path.exists(parent_map):
        parent_map = None

    return child_map, parent_map, qcow_file


# 子map文件，所有块地址: [1,2,3, 5,6, 8,10]
# 转换为连续的块: [(1, 3), (5, 2), (8, 1), (10, 1)]
def convert_map1_positions_to_uninterrupted_blocks(positions):
    result = []
    for k, g in groupby(enumerate(sorted(positions)), lambda x: x[0] - x[1]):
        group = list(map(itemgetter(1), g))
        result.append((group[0], len(group)))
    return result


def calculate_available_cores():
    jsonstr = boxService.box_service.queryTakeOverHostInfo('none')
    if jsonstr:
        kvm_virtual_cpus = json.loads(jsonstr)['used_cpu_number']
    else:
        kvm_virtual_cpus = 0

    logical_cpus = psutil.cpu_count()
    return int(logical_cpus - 2 - (kvm_virtual_cpus / 2))


def modify_current_map_last_line(map_lines):
    line_params = map_lines.pop().split(':')
    line_blocks = int(line_params[2]) * sector_size / block_size
    if line_blocks <= 1:
        return map_lines

    new_line_sector = int((line_blocks - 1) * block_size / sector_size)
    new_last_line = '{}:{}:{}{}'.format(line_params[0], line_params[1], new_line_sector, os.linesep)
    map_lines.append(new_last_line)
    return map_lines


# 压缩current_qcow, 或current_qcow的增量部分
def generate_compress_params_and_run_it(signal, current_qcow, current_map, start_line, limit_bytes, parent_map=None,
                                        from_api=False):
    """
    :param signal: 杀掉压缩进程的信号量
    :param current_qcow: 要被压缩的qcow文件
    :param current_map: 子map文件，同父map文件比较，得到子map文件特有的“块”
    :param start_line: 子map文件开始处理的行号
    :param limit_bytes: 子map文件本次处理多少增量字节
    :param parent_map: 父map文件
    :param from_api: 是否来自api的调用；或者作脚本执行
    :return: 压缩命令行，下一次开始行号；下一次开始行号，运行结果
    """
    current_map, parent_map, current_qcow = verify_map_qcow(current_map, parent_map, current_qcow)
    if current_map is None:  # do nothing
        return None, None

    map2_bitmap = generate_map_file_bitmap(parent_map, current_qcow)  # parent_map=None，生成其parent_bitmap=None
    map1_positions = []  # current_map增量的部分
    with open(current_map) as fin:
        total_lines = modify_current_map_last_line(fin.readlines())
        if not total_lines:
            return None, None
        lines_remain = total_lines[start_line:]
        for (line, num) in zip(lines_remain, range(start_line, len(total_lines))):
            positions = get_map1_line_particular_positions(line, map2_bitmap)
            map1_positions += positions

            if len(map1_positions) * block_size >= limit_bytes:
                next_line = num + 1
                break
        else:
            next_line = None

    map1_blocks = convert_map1_positions_to_uninterrupted_blocks(map1_positions)
    cluster_size, device_name = xsys.get_bsize_device_from_xfs_info(current_qcow)

    # '' 或者 '地址1,块数 地址2,块数 ...'
    blocks_info = ' '.join(['{0},{1}'.format(block_tup[0] * block_size, block_tup[1]) for block_tup in map1_blocks])
    cmd_line = r'{proc} -f {file} -a {arith} -l {rank} -s {block_size} -h {device} -p {cpu_logic} -k {kill_sig} ' \
               r'-t {bsize} -r {pio} -b {blocks_info}'. \
        format(proc=compact_cmd, file=current_qcow, arith=_arith, rank=_rank, block_size=block_size, device=device_name,
               cpu_logic=calculate_available_cores(), kill_sig=signal, bsize=cluster_size,
               pio=_pio, blocks_info=blocks_info) if blocks_info else None  # 'cmd line' or None

    if from_api:
        return cmd_line, next_line

    if cmd_line is not None:
        ret_code = os.system(cmd_line)
        print_logs('run cmd line: {}'.format(cmd_line))
    else:
        ret_code = 0
        print_logs('do nothing: map2 contain map1')

    return next_line, ret_code


# Ex: python runCmpr.py qcow-path map1-path line-start line-count -b map2-path
def get_cmd_args():
    args_parser = argparse.ArgumentParser(description="compress")
    args_parser.add_argument("qcow", help="qcow file path")
    args_parser.add_argument("map", help="map file path")
    args_parser.add_argument("line_start", help="to deal from line x")
    args_parser.add_argument("line_count", help="how many lines to deal")
    args_parser.add_argument("-b", help="map file path", default=None)
    return args_parser.parse_args()


if __name__ == "__main__":
    script_params = get_cmd_args()
    _next_line, _run_result = generate_compress_params_and_run_it(35, script_params.qcow, script_params.map,
                                                                  int(script_params.line_start),
                                                                  int(script_params.line_count),
                                                                  parent_map=script_params.b, from_api=False)
    print('next_line: {0}, run_result: {1}'.format(_next_line, _run_result))
