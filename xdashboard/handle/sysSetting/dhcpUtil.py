import os
import uuid
import socket


class DHCPConfigFile(object):
    def __init__(self, filepath):
        self.filepath = filepath

    @staticmethod
    def getMacAddress():
        node = uuid.getnode()
        _mac = uuid.UUID(int=node).hex[-12:]
        _mac = _mac[0:2] + ':' + _mac[2:4] + ':' + _mac[4:6] + ':' + _mac[6:8] + ':' + _mac[8:10] + ':' + _mac[10:12]
        return _mac

    @staticmethod
    def getIpAddress():
        try:
            csock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            csock.connect(('8.8.8.8', 80))
            (addr, port) = csock.getsockname()
            csock.close()
            return addr
        except socket.error:
            return "127.0.0.1"

    @staticmethod
    def generateSubnetAndNetmask(startIP, netMask):
        ip = startIP.split('.')
        mask = netMask.split('.')
        _subnet = '{}.{}.{}.{}'.format(int(ip[0]) & int(mask[0]),
                                       int(ip[1]) & int(mask[1]),
                                       int(ip[2]) & int(mask[2]),
                                       int(ip[3]) & int(mask[3]))
        _netmask = netMask
        return [_subnet, _netmask]

    # 覆盖式创建新文件，再初始化.
    def configInit(self, startIP, endIP, routers, subnet_mask, default_lease_time, max_lease_time):
        localMac = self.getMacAddress()
        localIp = self.getIpAddress()
        next_server = localIp
        fileName = '"grldr"'

        # 必须保证目录有效: .../.../dhcpd.conf
        dirname, filename = os.path.split(self.filepath)
        if not os.path.exists(dirname):
            os.makedirs(dirname)

        # 在该目录下新建dhcpd.conf文件
        with open(self.filepath, 'w') as fout:
            fout.write('#FileIsInitialized\n')
            fout.write('ignore client-updates;\n')
            fout.write('allow booting;\n')
            fout.write('allow bootp;\n\n')

            ls = self.generateSubnetAndNetmask(startIP, subnet_mask)
            fout.write('subnet ' + ls[0] + ' netmask ' + ls[1] + ' {\n')
            fout.write('  range ' + startIP + ' ' + endIP + ';\n')
            fout.write('  option routers ' + routers + ';\n')
            fout.write('  option subnet-mask ' + subnet_mask + ';\n')
            fout.write('  default-lease-time ' + default_lease_time + ';\n')
            fout.write('  max-lease-time ' + max_lease_time + ';\n')
            fout.write('  next-server ' + next_server + ';\n')
            fout.write('  filename ' + fileName + ';\n')
            fout.write('}\n\n')

            fout.write('host ens33 {\n')
            fout.write('  hardware ethernet ' + localMac + ';\n')
            fout.write('  fixed-address ' + localIp + ';\n')
            fout.write('}\n\n')

    # 以只读，打开存在的文件
    def getLineFromConfigFile(self, paramName):
        with open(self.filepath, 'r') as fin:
            for line in fin:
                if paramName in line:
                    return line
            raise Exception('getLineFromConfigFile(paramName): paramName spell error')

    def isFileInit(self):
        with open(self.filepath, 'r') as fin:
            return fin.readline().startswith('#FileIsInitialized')

    def getLocalMac(self):
        line = self.getLineFromConfigFile('hardware ethernet ')
        return line.split(';')[0].split(' ')[-1]

    def getLocalIp(self):
        line = self.getLineFromConfigFile('fixed-address ')
        return line.split(';')[0].split(' ')[-1]

    def getSubnet(self):
        line = self.getLineFromConfigFile('subnet ')
        return line.split(' ')[1]

    def getNetmask(self):
        line = self.getLineFromConfigFile('netmask ')
        return line.split(' ')[3]

    def getIpRange(self):
        line = self.getLineFromConfigFile('range ')
        strs = line.split(';')[0].split(' ')
        return [strs[-2], strs[-1]]

    def getRouters(self):
        line = self.getLineFromConfigFile('option routers ')
        return line.split(';')[0].split(' ')[-1]

    def getSubnetMask(self):
        line = self.getLineFromConfigFile('option subnet-mask ')
        return line.split(';')[0].split(' ')[-1]

    def getDefaultLeaseTime(self):
        line = self.getLineFromConfigFile('default-lease-time ')
        return line.split(';')[0].split(' ')[-1]

    def getMaxLeaseTime(self):
        line = self.getLineFromConfigFile('max-lease-time ')
        return line.split(';')[0].split(' ')[-1]

    def getNextServer(self):
        line = self.getLineFromConfigFile('next-server ')
        return line.split(';')[0].split(' ')[-1]

    def getFilename(self):
        line = self.getLineFromConfigFile('filename ')
        return line.split(';')[0].split(' ')[-1]
