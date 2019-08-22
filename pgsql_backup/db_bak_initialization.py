import subprocess

PGDATA = '/var/db/pgsql/data/'


def check_already_yes_or_no_configuration():
    result = open(PGDATA + 'postgresql.conf', 'r+')
    result_all_lines = result.readlines()
    result.seek(0)
    result.truncate()
    temp = 1
    for line in result_all_lines:
        if 'wal_level = archive' in line:
            temp = 0
        result.write(line)
    result.close()
    return temp


def bak_configuration_parameter():
    check_result = check_already_yes_or_no_configuration()
    if check_result == 1:
        # 对postgresql.conf的配置
        result = open(PGDATA + 'postgresql.conf', 'r+')
        result_all_lines = result.readlines()
        result.seek(0)
        result.truncate()
        for line in result_all_lines:
            try:
                if '#wal_level = minimal' in line:
                    line = line.replace('#wal_level = minimal', 'wal_level = archive')
                    result.write(line)
                elif '#archive_mode = off' in line:
                    line = line.replace('#archive_mode = off', 'archive_mode = on')
                    result.write(line)
                # elif "#listen_addresses = 'localhost'" in line:
                #    line = line.replace("#listen_addresses = 'localhost'", "listen_addresses = '*'")
                #    result.write(line)
                elif '#max_wal_senders = 0' in line:
                    line = line.replace('#max_wal_senders = 0', 'max_wal_senders = 8')
                    result.write(line)
                elif '#archive_command = ' in line:
                    archive_command_cmd = 'archive_command = \'/var/db/pgsql/data/archive.sh %p %f\''
                    line = line.replace(line, archive_command_cmd)
                    result.write(line)
                else:
                    result.write(line)
            except:
                print("配置文件已经损坏")
        result.close()
        # 对pg_hba.conf的配置
        result1 = open(PGDATA + 'pg_hba.conf', 'r+')
        result_all_lines1 = result1.readlines()
        result1.seek(0)
        result1.truncate()
        for line in result_all_lines1:
            try:
                if 'replication privilege.' in line:
                    line = line.replace(line, 'host replication rep 0.0.0.0/0 trust\n')
                    result1.write(line)
                else:
                    result1.write(line)
            except:
                print("配置文件已经损坏")
        result1.close()
        subprocess.call('systemctl start WatchPowerServ', shell=True)
        subprocess.call('systemctl start postgresql', shell=True)
        create_bak_role = "export PGPASSWORD=f;psql -h 127.0.0.1 -p 21114 -U postgres postgres " \
                          "-c \"create role rep nosuperuser replication login connection limit 32 " \
                          "encrypted password '123rep123'\""
        subprocess.call(create_bak_role, shell=True)
    else:
        print('数据库备份已经初始化了')
        return


if __name__ == "__main__":
    pass
    # subprocess.call('systemctl stop WatchPowerServ', shell=True)
    # subprocess.call('systemctl stop postgresql', shell=True)
    # bak_configuration_parameter()
    # subprocess.call('systemctl start WatchPowerServ', shell=True)
    # subprocess.call('systemctl start postgresql', shell=True)
