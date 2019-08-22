# coding=utf-8
from xdashboard.models import DataDictionary,TmpDictionary

def SaveDictionary(type,key,value):
    ret={"r":0,"e":"操作成功"}
    onedata = DataDictionary.objects.filter(dictType=type,dictKey=key)
    if onedata:
        try:
            onedata.update(dictType=type, dictKey=key, dictValue=value)
        except Exception as e:
            ret = {"r":1,"e":str(e)}
    else:
        try:
            DataDictionary.objects.create(dictType=type, dictKey=key, dictValue=value)
        except Exception as e:
            ret = {"r": 1, "e": str(e)}

    return ret

def GetDictionary(type,key):
    onedata = DataDictionary.objects.filter(dictType=type, dictKey=key)
    if onedata:
        return {"r":0,"value":onedata[0].dictValue}
    return {"r":1,"e":"未找到"}

def DelDictionary(type,key):
    onedata = DataDictionary.objects.filter(dictType=type, dictKey=key)
    if onedata:
        onedata.delete()
        return {"r":0,"e":"操作成功"}
    return {"r":1,"e":"未找到"}

def DelDictionaryByType(type):
    onedata = DataDictionary.objects.filter(dictType=type)
    if onedata:
        onedata.delete()
        return {"r":0,"e":"操作成功"}
    return {"r":1,"e":"未找到"}

def GetDictionaryByTpye(type):
    return DataDictionary.objects.filter(dictType=type)

def GetDictionary(type,key,default):
    onedata = DataDictionary.objects.filter(dictType=type, dictKey=key)
    if onedata:
        return onedata[0].dictValue
    return default

def GetTmpDictionary(type,key):
    onedata = TmpDictionary.objects.filter(dictType=type, dictKey=key)
    if onedata:
        return {"r": 0, "value": onedata[0].dictValue,"expireTime":onedata[0].expireTime}
    return {"r": 1, "e": "未找到"}

def SaveTmpDictionary(type,key,value,expire):
    ret={"r":0,"e":"操作成功"}
    onedata = TmpDictionary.objects.filter(dictType=type,dictKey=key)
    if onedata:
        try:
            onedata.update(dictType=type, dictKey=key, dictValue=value,expireTime=expire)
        except Exception as e:
            ret = {"r":1,"e":str(e)}
    else:
        try:
            TmpDictionary.objects.create(dictType=type, dictKey=key, dictValue=value,expireTime=expire)
        except Exception as e:
            ret = {"r": 1, "e": str(e)}

    return ret