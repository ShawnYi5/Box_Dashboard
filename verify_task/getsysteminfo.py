import ctypes
import json
from ctypes import c_char_p


def get_pe_systeminfo():
    try:
        pdll = ctypes.CDLL(r'SystemInfo.dll')
        pdll.GetComputerAllInfo_for_PE.restype = c_char_p
    except Exception as e:
        return {'r': -1, 'e': str(e)}

    ret = pdll.GetComputerAllInfo_for_PE()
    return {'r': 0, 'systeminfo': json.loads(ret.decode('gb2312'))}


if __name__ == "__main__":
    jsonobj = get_pe_systeminfo()
    print(json.dumps(jsonobj, ensure_ascii=False))
