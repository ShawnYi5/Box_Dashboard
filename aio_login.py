import os
import json
import sys
import traceback

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "box_dashboard.settings")
CIPHERTEXT_FILE = '/etc/aio/password_ciphertext.json'
from django.contrib.auth.hashers import check_password


def check_login_password(password):
    with open(CIPHERTEXT_FILE, 'r') as f:
        result = json.load(f)
    ciphertext = result['password_ciphertext']
    if check_password(password, ciphertext):
        return True
    else:
        return False


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('not input chk pwd')
        sys.exit(0)
    try:
        if check_login_password(sys.argv[1]):
            print('succ')
        else:
            print('failed')
    except:
        print(traceback.format_exc())
