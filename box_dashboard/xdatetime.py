#!/usr/bin/env python3

from datetime import datetime

FORMAT_ONLY_DATE = '%Y-%m-%d'
FORMAT_WITH_SECOND = '%Y-%m-%dT%H:%M:%S'
FORMAT_WITH_MICROSECOND = '%Y-%m-%dT%H:%M:%S.%f'
FORMAT_WITH_MICROSECOND_2 = '%Y-%m-%d %H:%M:%S.%f'
FORMAT_WITH_SECOND_FOR_PATH = '%Y_%m_%dT%H_%M_%S'
FORMAT_WITH_USER_SECOND = '%Y-%m-%d %H:%M:%S'



def string2datetime(s):
    if s is None:
        return None

    s_len = len(s)
    if s_len == 10:  # etc : '2000-01-01'
        return datetime.strptime(s, FORMAT_ONLY_DATE)
    elif s_len == 19:  # etc : '2000-01-01T00:00:00'
        return datetime.strptime(s, FORMAT_WITH_SECOND)
    elif 21 <= s_len <= 26:  # etc : '2000-01-01T00:00:00.0' '2000-01-01T00:00:00.999999'
        try:
            return datetime.strptime(s, FORMAT_WITH_MICROSECOND)
        except:
            return datetime.strptime(s, FORMAT_WITH_MICROSECOND_2)
    else:
        raise r'string2datetime unsupport : {}'.format(s)
