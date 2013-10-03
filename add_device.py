import random
import sys
import string

def rid(N):
    return ''.join(random.choice(string.ascii_uppercase + string.digits) for x in range(N))

def add_device():
    dc = dmd.Devices.createOrganizer('/Server/MySQL')
    dc.setZenProperty('zPythonClass', 'ZenPacks.zenoss.MySqlMonitor.MySQLServer')
    name = 'mysql_' + rid(3)
    device = dc.createInstance(name)
    device.setPerformanceMonitor('localhost')
    device.manageIp = '127.0.0.1'
    device.index_object()
    commit()
    return name

name = add_device()
print name
