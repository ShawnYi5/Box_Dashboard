# coding=utf-8
# pip install requests
import requests, os, base64, json, hashlib, sys, urllib, time
import enum_id
import sqlite3

updateurl = 'http://update.clerware.com'


# 大文件的MD5值
def GetFileMd5(filename, ostype):
    if not os.path.isfile(filename):
        return MD5(str(ostype))
    md5 = os.path.splitext(os.path.basename(filename))
    return md5[0]


def CalcSha1(filepath):
    with open(filepath, 'rb') as f:
        sha1obj = hashlib.sha1()
        sha1obj.update(f.read())
        hash = sha1obj.hexdigest()
        return hash


def CalcMD5(filepath):
    with open(filepath, 'rb') as f:
        md5obj = hashlib.md5()
        md5obj.update(f.read())
        hash = md5obj.hexdigest()
        return hash


def myReadFile(hashstr, filepath):
    start = 0
    step = 1024 * 1024
    filesize = os.path.getsize(filepath)
    drivername = os.path.basename(filepath)
    file_object = open(filepath, 'rb')
    try:
        while True:
            chunk = file_object.read(step)
            if not chunk:
                break
            myPostBin(chunk, str(start), str(step), str(drivername), str(hashstr), str(filesize))
            start = start + step
    finally:
        file_object.close()


def myPostBin(chunk, start, step, drivername, hashstr, filesize):
    url = updateurl + '/index.php/handler/driver/?a=upload&' + '&start=' + start + '&step=' + step + '&total=' + filesize
    url += '&hash=' + hashstr
    url += '&filename=' + drivername
    print(url)
    while True:
        try:
            jsonstr = ''
            r = requests.post(url, ';base64,' + str(base64.b64encode(chunk).decode()))
            jsonstr = str(r.content, encoding="utf-8")
            j = json.loads(jsonstr)
            if j['r'] == 0:
                pass
            else:
                print(j['e'])
            break
        except Exception as e:
            print(str(e) + ":" + jsonstr)
            time.sleep(30)


def MD5(src):
    m2 = hashlib.md5()
    m2.update(src.encode('utf-8'))
    return m2.hexdigest()


def myPostDriver(drivername, hashstr, filepath, org_path, ostype, hardidarr, compatibleidarr):
    url = updateurl + '/index.php/handler/driver/?a=add'
    print(url)
    post_data = dict()
    post_data["filename"] = drivername
    post_data["os"] = ostype
    post_data["hardidarr"] = hardidarr
    post_data["compatibleidarr"] = compatibleidarr
    post_data["waite_upload"] = 'waite_upload'
    if filepath == 'none':
        post_data["waite_upload"] = 'none'
    post_data["hash"] = hashstr
    post_data["org_path"] = org_path
    while True:
        try:
            jsonstr = ''
            r = requests.post(url, data=post_data)
            jsonstr = str(r.content, encoding="utf-8")
            j = json.loads(jsonstr)
            if j['r'] == 0 and filepath != 'none':
                myReadFile(hashstr, filepath)
            else:
                print(j['e'])
            break
        except Exception as e:
            print(str(e) + ":" + jsonstr)
            time.sleep(30)


def myUpdateDriver(drivername, filemd5, ostype, hardidarr, compatibleidarr):
    url = updateurl + '/index.php/handler/driver/?a=update'
    print(url)
    post_data = dict()
    post_data["filename"] = drivername
    post_data["hash"] = filemd5
    post_data["os"] = ostype
    post_data["hardidarr"] = hardidarr
    post_data["compatibleidarr"] = compatibleidarr

    while True:
        try:
            jsonstr = ''
            r = requests.post(url, data=post_data)
            jsonstr = str(r.content, encoding="utf-8")
            j = json.loads(jsonstr)
            if j['r'] == 0:
                pass
            else:
                print(j['e'])
            break
        except Exception as e:
            print(str(e) + ":" + jsonstr)
            time.sleep(30)


def myPostHardidExtension(extension):
    url = updateurl + '/index.php/handler/hardid/?a=addextension'
    print(url)
    post_data = dict()
    post_data['ex'] = extension
    while True:
        try:
            jsonstr = ''
            r = requests.post(url, data=post_data)
            jsonstr = str(r.content, encoding="utf-8")
            j = json.loads(jsonstr)
            if j['r'] == 0:
                pass
            else:
                print(j['e'])
            break
        except Exception as e:
            print(str(e) + ":" + jsonstr)
            time.sleep(30)


def isFileExist(hashstr):
    url = updateurl + '/index.php/handler/driver/?a=isexist&hash=' + hashstr
    print(url)
    while True:
        try:
            jsonstr = ''
            r = requests.post(url)
            jsonstr = str(r.content, encoding="utf-8")
            j = json.loads(jsonstr)
            if j['r'] == 0:
                return j['exist']
            else:
                print(j['e'])
            break
        except Exception as e:
            print(str(e) + ":" + jsonstr)
            time.sleep(30)

    return False


def enum_id_and_post(db_path, driver_pool, driver_pool_update, scan_type):
    if scan_type == "scan_one_dir":
        enum_id.scan_one_dir(db_path, driver_pool, driver_pool_update)
    elif scan_type == "scan_no_guid":
        enum_id.scan_no_guid(db_path, driver_pool, driver_pool_update)
    elif scan_type == "scan_micro":
        enum_id.scan_micro(db_path, driver_pool, driver_pool_update)
    else:
        enum_id.scan(db_path, driver_pool, driver_pool_update)
    enum_id.gen_zip(db_path, driver_pool, driver_pool_update)
    driverlist = enum_id.get_list(db_path, driver_pool, driver_pool_update)
    postvec = dict()
    for driver in driverlist:
        if driver["del"] == 1:
            continue
        if driver['system_name'] is None:
            driver['system_name'] = 'none'
        if driver['inf_driver_ver'] is None:
            driver['inf_driver_ver'] = '0'
        if driver['inf_path'] is None:
            driver['inf_path'] = 'none'
        driverdist = dict()
        hard_or_compid = driver["hard_or_comp_id"]
        zippath = driver["zip_path"]
        if zippath == None:
            zippath = 'none'
        driverdist["hard_or_comp_id"] = hard_or_compid
        show_name = driver["show_name"]
        if show_name == None:
            show_name = hard_or_compid
        driverdist["show_name"] = show_name
        driverdist["system_name"] = driver["system_name"]
        tmpostype = list()
        os_id = -1
        enum_id.getOSType(tmpostype, driver["system_name"])
        if len(tmpostype) == 1:
            os_id = tmpostype[0]
        driverdist["os_id"] = os_id
        driverdist["inf_driver_ver"] = driver["inf_driver_ver"]
        driverdist["inf_path"] = driver["inf_path"]
        driverdist["IsMicro"] = driver["IsMicro"]
        driverdist["score"] = driver["score"]
        driverdist["HWPlatform"] = driver["HWPlatform"]
        if driver["depends"] is None:
            driver["depends"] = ''
        driverdist["depends"] = driver["depends"]
        if driver["e_i_1"] is None:
            driver["e_i_1"] = 0
        if driver["e_i_2"] is None:
            driver["e_i_2"] = 0
        if driver["e_s_1"] is None:
            driver["e_s_1"] = ''
        if driver["e_s_2"] is None:
            driver["e_s_2"] = ''
        driverdist["e_i_1"] = driver["e_i_1"]
        driverdist["e_i_2"] = driver["e_i_2"]
        driverdist["e_s_1"] = driver["e_s_1"]
        driverdist["e_s_2"] = driver["e_s_2"]
        if zippath not in postvec:
            postvec[zippath] = list()
        postvec[zippath].append(driverdist)
        if len(postvec) >= 2000:
            myPostDriver_vec(postvec)
            postvec = dict()
    myPostDriver_vec(postvec)
    postvec = dict()


def myPostDriver_vec(postvec):
    extensionvec = dict()
    for zipname, hardids in postvec.items():
        filepath = os.path.join(driver_pool_update, zipname)
        ostype = list()
        compatibleidarr = ''
        drivername = ''
        hardidvec = list()
        for id in hardids:
            ostype = enum_id.getOSType(ostype, id["system_name"])
            hardidvec.append(id["hard_or_comp_id"])
            if drivername == '':
                drivername = id["show_name"] + " " + id["system_name"] + "(" + str(len(hardids)) + ")"
        hardidarr = ','.join(hardidvec)
        filemd5 = GetFileMd5(filepath, ostype)
        ostype = ','.join(ostype)
        org_path = zipname;
        if not isFileExist(filemd5):
            if zipname == 'none':
                filepath = 'none'
                org_path = ostype
            myPostDriver(drivername, filemd5, filepath, org_path, ostype, hardidarr, compatibleidarr)
        else:
            print('文件已在外网存在，文件名：{}'.format(filepath))
            myUpdateDriver(drivername, filemd5, ostype, hardidarr, compatibleidarr)

        extensionvec[filemd5] = hardids

        for hash, extension in extensionvec.items():
            tmpextensionvec = list()
            i = 0
            for ext in extension:
                ext['hash'] = hash
                tmpextensionvec.append(ext)
                i = i + 1
                if i > 1000:
                    exstr = json.dumps(tmpextensionvec, ensure_ascii=False)
                    myPostHardidExtension(exstr)
                    tmpextensionvec = list()
                    i = 0
            if i > 0:
                exstr = json.dumps(tmpextensionvec, ensure_ascii=False)
                myPostHardidExtension(exstr)
                tmpextensionvec = list()
                i = 0
        extensionvec = dict()


def factory_update_one_driver(cu, jsonfile, db_path):
    jsonobj = None
    with open(os.path.join(jsonfile)) as file_object:
        jsonobj = json.load(file_object)
    all_sql_info_list = list()
    for item in jsonobj:
        one_sql_info_list = {'server_id': None, 'del': None, 'show_name': None, 'hard_or_comp_id': None,
                             'inf_driver_ver': None, 'inf_path': None, 'zip_path': None, 'system_name': None, 'del': 0}
        one_sql_info_list["server_id"] = item["server_id"]
        one_sql_info_list["hard_or_comp_id"] = item["hard_or_comp_id"]
        one_sql_info_list["inf_driver_ver"] = item["inf_driver_ver"]
        one_sql_info_list["inf_path"] = item["inf_path"]
        one_sql_info_list["system_name"] = item["system_name"]
        one_sql_info_list["show_name"] = item["show_name"]
        one_sql_info_list["del"] = item["del"]
        if item["zip_path"] == None:
            one_sql_info_list["zip_path"] = None
        else:
            one_sql_info_list["zip_path"] = item["org_path"]
        all_sql_info_list.append(one_sql_info_list.copy())
    enum_id.factory_update_one_zip_of_db(cu, all_sql_info_list, db_path)


def genFactoryDB(folder, db_path):
    folders = os.listdir(folder)
    with sqlite3.connect(db_path) as cx:
        cu = cx.cursor()
        for name in folders:
            curname = os.path.join(folder, name)
            isfile = os.path.isfile(curname)
            if isfile:
                ext = os.path.splitext(curname)[1]
                if ext == '.json':
                    print('factory_update_one_driver curname={}'.format(curname))
                    factory_update_one_driver(cu, curname, db_path)
        cmd = "insert into id_table (server_id,del,show_name,hard_or_comp_id,inf_driver_ver,inf_path,zip_path,system_name,type)\
              select distinct server_id,del,show_name,hard_or_comp_id,inf_driver_ver,inf_path,zip_path,system_name,type from tmp_id_table"
        print(cmd)
        cu.execute(cmd)
        cmd = "delete from tmp_id_table"
        print(cmd)
        cu.execute(cmd)
        cx.commit()


if __name__ == '__main__':
    # 步骤1.上传驱动到外网服务器
    if -1 != sys.argv[1].find("upload"):
        db_path = sys.argv[2]
        driver_pool = sys.argv[3]
        driver_pool_update = sys.argv[4]
        updateurl = sys.argv[5]
        enum_id_and_post(db_path, driver_pool, driver_pool_update, 'scan')
    if -1 != sys.argv[1].find("scan_one_dir"):
        db_path = sys.argv[2]
        driver_pool = sys.argv[3]
        driver_pool_update = sys.argv[4]
        updateurl = sys.argv[5]
        enum_id_and_post(db_path, driver_pool, driver_pool_update, 'scan_one_dir')
    if -1 != sys.argv[1].find("no_guid"):
        db_path = sys.argv[2]
        driver_pool = sys.argv[3]
        driver_pool_update = sys.argv[4]
        updateurl = sys.argv[5]
        enum_id_and_post(db_path, driver_pool, driver_pool_update, 'scan_no_guid')
    if -1 != sys.argv[1].find("scan_micro"):
        db_path = sys.argv[2]
        driver_pool = sys.argv[3]
        driver_pool_update = sys.argv[4]
        updateurl = sys.argv[5]
        enum_id_and_post(db_path, driver_pool, driver_pool_update, 'scan_micro')
    if -1 != sys.argv[1].find("DelServ"):
        del_drv_pool = sys.argv[2]
        updateurl = sys.argv[3]
        enum_id.DelServ(del_drv_pool, updateurl)

    # 生成工厂数据库
    # 步骤2.http://172.16.1.10/index.php/api/drivers/?a=getfactorydb&callback=t
    # 步骤3.解压密码 {89B87785-0C5F-47eb-B7DE-73DD962B0FAE}
    if -1 != sys.argv[1].find("genFactoryDB"):
        zip_folder = sys.argv[2]
        db_path = sys.argv[3]
        genFactoryDB(zip_folder, db_path)
