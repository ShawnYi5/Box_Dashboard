# offlineStorage 离线存储-送检版本
# backupobj 备份对象-送检版本
# nolinux 客户端不支持linux-送检版本
# smbencrypt SMB加密的提示
# closeagentcompress 关闭Agent传输压缩数据
# version1 版本号为1.0-送检版本
# novalidate 无验证菜单-送检版本
# nolicense_UI 无授权管理界面
# nomigrate_UI 无迁移界面
# nouser_mgr 无用户管理
# no_oplog admin-[用户管理]中无删除操作日志分配权限，中冶需要配合此参数
# no_clientlog admin-[用户管理]中无删除客户端日志分配权限，中冶需要配合此参数
# no_make_media 无制作启动介质
# no_fast_boot 不使用快速重建
# clw_desktop_aio 科力锐桌面保障恢复一体机
# 安装时自动填写hasFunctional，仅用于授权中没有填写的项
def hasFunctional(module):
    if module in ():
        return True
