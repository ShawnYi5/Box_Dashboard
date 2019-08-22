from intervaltree import Interval, IntervalTree
from box_dashboard import xlogging, boxService
import subprocess

_logger = xlogging.getLogger(__name__)

g_use_test_file = False


def log_warm_msg(msg):
    if g_use_test_file:
        print("[warm] " + str(msg))
    else:
        _logger.warning(str(msg))
    return


def log_info_msg(msg):
    if g_use_test_file:
        print("[info] " + str(msg))
    else:
        _logger.info(str(msg))
    return


def log_dbg_msg(msg):
    if g_use_test_file:
        print("[dbg] " + str(msg))
    return


def log_err_msg(msg):
    if g_use_test_file:
        print("[err] " + str(msg))
    else:
        _logger.error(str(msg))
    return


def str_to_integer(str_val):
    val = str_val.strip().lower()
    if val.startswith("0x"):
        return int(val[2:], 16)
    else:
        return int(str_val)


def call_one_item_cb(line, item_cb, cb_context):
    sp = line.split(":")
    count = len(sp)

    if g_use_test_file:
        if count != 3:
            log_err_msg("[call_one_item_cb] count={} not equal 3".format(count))
            return
        begin = str_to_integer(sp[0])
        qcow_off = str_to_integer(sp[1])
        length = str_to_integer(sp[2]) * 512
        end = begin + length
    else:
        if count != 4:
            log_err_msg("[call_one_item_cb] count={} not equal 4".format(count))
            return
        begin = str_to_integer(sp[0])
        end = str_to_integer(sp[1])
        qcow_off = str_to_integer(sp[3])

    """"
    log_dbg_msg("[call_one_item_cb] bgn={} qcow={} len={} end={}".format(hex(begin),
    hex(qcow_off), int(length / 512), hex(end)))
    """
    item_cb(cb_context, begin, end, qcow_off)


def read_snapshot_bitmap(map_file, item_cb, cb_context):
    log_info_msg("[read_snapshot_bitmap] map_file={}".format(map_file))

    try:
        read_cnt = 0
        with open(map_file, 'rt') as f:
            for line in f:
                call_one_item_cb(line, item_cb, cb_context)
                read_cnt += 1

        log_dbg_msg("[read_snapshot_bitmap] read_cnt={}".format(read_cnt))
        return True
    except Exception as e:
        log_err_msg("[read_snapshot_bitmap] exception={}".format(e))
        return False


def add_by_lba_cb(t, begin, end, qcow_off):
    t.add(Interval(begin, end, qcow_off))


def add_by_qcow2_cb(t, begin, end, qcow_off):
    if qcow_off == 0:
        log_err_msg("[add_by_qcow2_cb] invalid qcow_off")
        return

    q_bgn = qcow_off
    q_end = qcow_off + (end - begin)
    is_lower = 0
    data = [q_bgn, q_end, begin, end, is_lower]

    t.add(Interval(q_bgn, q_end, data))


def remove_exist_iv(t, iv):
    try:
        t.remove(iv)
        return True
    except Exception as e:
        return False


def chop_func_cb(iv, islower):
    data = iv.data
    if islower:
        data[4] = 1
    else:
        data[4] = 2
    return data


def exclude_by_qcow2(curr_tree, begin, end, qcow_off):
    q_bgn = qcow_off
    q_end = qcow_off + (end - begin)
    is_lower = 0

    data = [q_bgn, q_end, begin, end, is_lower]

    iv = Interval(q_bgn, q_end, data)
    retval = remove_exist_iv(curr_tree, iv)
    if retval:
        log_dbg_msg("[exclude_by_qcow2] remove bgn={} qcow={}".format(hex(begin), hex(qcow_off)))
        return

    curr_tree.chop(q_bgn, q_end, chop_func_cb)


def get_single_iv_tree(curr_path):
    log_info_msg("[get_single_iv_tree] enter")
    curr_tree = IntervalTree()
    retval = read_snapshot_bitmap(curr_path, add_by_lba_cb, curr_tree)
    if not retval:
        # log_err_msg("[get_single_iv_tree] read_snapshot_bitmap failed")
        xlogging.raise_and_logging_error(r'读取位图文件失败', r'[get_single_iv_tree] get read_snapshot_bitmap failed')
        return None

    count = len(curr_tree)
    log_dbg_msg("[get_single_iv_tree] count={}".format(count))

    return curr_tree


def qcow2_to_lba(qcow2_tree, lba_tree):
    for qcow2 in qcow2_tree:
        q_data = qcow2.data
        q_bgn = q_data[0]
        q_end = q_data[1]
        l_bgn = q_data[2]
        l_end = q_data[3]
        is_lower = q_data[4]

        if (qcow2.begin < q_bgn) or (qcow2.end > q_end):
            xlogging.raise_and_logging_error(r'位图数据不正常',
                                             r'[qcow2_to_lba] not equal begin, begin={} g_bgn={} end={} q_end={}'.format(
                                                 hex(qcow2.begin), hex(q_bgn), hex(qcow2.end), hex(q_end)))
            return False

        offset = qcow2.begin - q_bgn
        l_bgn += offset

        offset = q_end - qcow2.end
        l_end -= offset

        lba_tree.add(Interval(l_bgn, l_end, qcow2.begin))

    return True


def get_bitmap_iv_tree(curr_path, prev_path):
    if not prev_path:
        return get_single_iv_tree(curr_path)

    curr_tree = IntervalTree()
    retval = read_snapshot_bitmap(curr_path, add_by_qcow2_cb, curr_tree)
    if not retval:
        xlogging.raise_and_logging_error(r'读取位图文件 1 失败', r'[get_snapshot_inc_bitmap] get curr_path failed')
        return None

    retval = read_snapshot_bitmap(prev_path, exclude_by_qcow2, curr_tree)
    if not retval:
        xlogging.raise_and_logging_error(r'读取位图文件 2 失败', r'[get_snapshot_inc_bitmap] get curr_path failed')
        return None

    lba_tree = IntervalTree()
    retval = qcow2_to_lba(curr_tree, lba_tree)
    curr_tree.clear()
    if not retval:
        log_err_msg("[get_bitmap_iv_tree] qcow2_to_lba failed")
        return None

    return lba_tree


def align_lba_offst(t):
    algin_size = 65536
    algin = IntervalTree()

    for iv in t:
        bgn = int(iv.begin / algin_size) * algin_size
        end = int((iv.end + algin_size - 1) / algin_size) * algin_size
        algin.add(Interval(bgn, end))

    log_dbg_msg("[align_lba_offst] algin={}".format(algin))

    # algin.merge_overlaps()
    return algin


def get_bmp_from_ivtree(t):
    align_t = align_lba_offst(t)
    t.clear()
    if not align_t:
        return None

    log_dbg_msg("[get_bmp_from_ivtree] align_t={}".format(align_t))

    bmp_list = []
    sort_t = sorted(align_t)
    for iv in sort_t:
        log_dbg_msg("[get_bmp_from_ivtree] bgn={} end={}".format(hex(iv.begin), hex(iv.end)))
        bmp_list.append([iv.begin, iv.end - iv.begin])

    return bmp_list


def get_snapshot_inc_bitmap_imp(curr_path, prev_path):
    log_dbg_msg("[get_snapshot_inc_bitmap_imp] begin")

    log_dbg_msg("[get_snapshot_inc_bitmap_imp] curr_path={}".format(curr_path))
    log_dbg_msg("[get_snapshot_inc_bitmap_imp] prev_path={}".format(prev_path))

    t = get_bitmap_iv_tree(curr_path, prev_path)
    if not t:
        log_err_msg("[get_snapshot_inc_bitmap_imp] get_bitmap_iv_tree failed")
        return None

    bmp_list = get_bmp_from_ivtree(t)
    if not bmp_list:
        log_err_msg("[get_snapshot_inc_bitmap_imp] get_bmp_from_ivtree failed")
        return None

    log_dbg_msg("[get_snapshot_inc_bitmap_imp] finish")
    return bmp_list


def get_map_file_path(snapshot_id):
    if not snapshot_id:
        log_err_msg("[get_map_file_path] snapshot_id is null")
        return None

    try:
        map_file = boxService.box_service.GetOnSnMapFile(snapshot_id)
        return map_file
    except Exception as e:
        log_err_msg("[get_map_file_path] Exception={}".format(e))
        return None


""""
[dbg] begin=0x100000 length=0x30000
[dbg] begin=0x13f0000 length=0x10000
[dbg] begin=0x8a400000 length=0x10000
[dbg] begin=0x43a600000 length=0x10000
[dbg] begin=0x43af00000 length=0x110000
"""


# 偏移量是长度的单位都是字节
# [[bgn1,len1], [bgn2,len2], [bgn3,len3]]
def get_snapshot_inc_bitmap(curr_snapshot, prev_snapshot):
    log_info_msg("[get_snapshot_inc_bitmap] begin")

    curr_path = get_map_file_path(curr_snapshot)
    prev_path = get_map_file_path(prev_snapshot)

    log_info_msg("[get_snapshot_inc_bitmap] curr_path={}".format(curr_path))
    log_info_msg("[get_snapshot_inc_bitmap] prev_path={}".format(prev_path))

    if not curr_path:
        xlogging.raise_and_logging_error(r'找不到备份位图文件 1', r'[get_snapshot_inc_bitmap] get curr_path failed')
        return []

    if prev_snapshot and (not prev_path):
        xlogging.raise_and_logging_error(r'找不到备份位图文件 2', r'[get_snapshot_inc_bitmap] get curr_path failed')
        return []

    bmp_list = get_snapshot_inc_bitmap_imp(curr_path, prev_path)
    if not bmp_list:
        log_warm_msg("[get_snapshot_inc_bitmap] get_snapshot_inc_bitmap_imp return None")
        return []

    log_info_msg("[get_snapshot_inc_bitmap] success")

    return bmp_list


def exec_cmd(cmd, cwd=None):
    std_out = []
    std_err = []

    log_info_msg("[exec_cmd] cmd={}".format(cmd))

    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, universal_newlines=True, shell=True, cwd=cwd,
                         stderr=subprocess.PIPE)
    p.wait()

    for line in p.stdout:
        std_out.append(line.rstrip())

    for line in p.stderr:
        std_err.append(line.rstrip())

    return p.returncode, std_out, std_err


def get_snapshot_inc_bitmap_V2(curr_snapshot, prev_snapshot, outfile):
    curr_path = get_map_file_path(curr_snapshot)
    prev_path = get_map_file_path(prev_snapshot)

    # log_info_msg("[get_snapshot_inc_bitmap] outfile={}".format(outfile))
    # log_info_msg("[get_snapshot_inc_bitmap] curr_path={}".format(curr_path))
    # log_info_msg("[get_snapshot_inc_bitmap] prev_path={}".format(prev_path))

    if prev_path:
        cmd = "/sbin/aio/incbmp_helper {} {} {}".format(curr_path, outfile, prev_path)
    else:
        cmd = "/sbin/aio/incbmp_helper {} {}".format(curr_path, outfile)

    cmd += " > /var/log/aio/incbmp_helper.log 2>&1"

    retval, out, err = exec_cmd(cmd)
    if retval != 0:
        log_err_msg("[get_snapshot_inc_bitmap_V2] exec_cmd failed, cmd={}".format(cmd))
        return False

    return True


if __name__ == "__main__":
    g_use_test_file = True

    curr_snap_path = "E:\\x\\new.map"
    prev_snap_path = "E:\\x\\old.map"

    bmp = get_snapshot_inc_bitmap_imp(curr_snap_path, prev_snap_path)
    if bmp:

        print("")
        log_dbg_msg(str(bmp))
        print("")

        for i in range(len(bmp)):
            b = bmp[i]
            log_dbg_msg("begin={} length={}".format(hex(b[0]), hex(b[1])))
    else:
        log_err_msg("get_snapshot_inc_bitmap_imp failed...")
