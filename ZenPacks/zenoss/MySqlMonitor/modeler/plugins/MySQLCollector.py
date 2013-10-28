##############################################################################
#
# Copyright (C) Zenoss, Inc. 2013, all rights reserved.
#
# This content is made available according to terms specified in
# License.zenoss under the directory where your Zenoss product is installed.
#
##############################################################################

''' Models discovery tree for MySQL. '''

import collections
import zope.component
from itertools import chain
from MySQLdb import cursors
from twisted.enterprise import adbapi
from twisted.internet import defer

from Products.DataCollector.plugins.CollectorPlugin import PythonPlugin
from Products.DataCollector.plugins.DataMaps import ObjectMap, RelationshipMap
from Products.ZenCollector.interfaces import IEventService
from ZenPacks.zenoss.MySqlMonitor import MODULE_NAME, NAME_SPLITTER
from ZenPacks.zenoss.MySqlMonitor.modeler import queries

from ZenPacks.zenoss.MySqlMonitor.utils import parse_mysql_connection_string


class MySQLCollector(PythonPlugin):
    '''
    PythonCollector plugin for modelling device components
    '''
    is_clear_run = True

    _eventService = zope.component.queryUtility(IEventService)

    deviceProperties = PythonPlugin.deviceProperties + (
        'zMySQLConnectionString',
        )

    queries = {
        'server': queries.SERVER_QUERY,
        'server_size': queries.SERVER_SIZE_QUERY,
        'master': queries.MASTER_QUERY,
        'slave': queries.SLAVE_QUERY,
        'db': queries.DB_QUERY
    }

    @defer.inlineCallbacks
    def collect(self, device, log):
        log.info("Collecting data for device %s", device.id)
        try:
            servers = parse_mysql_connection_string(
                device.zMySQLConnectionString)
        except ValueError, error:
            self.is_clear_run = False
            log.error(error.message)
            self._send_event(error.message, device.id, 5)
            return

        result = []
        for el in servers.values():
            dbpool = adbapi.ConnectionPool(
                "MySQLdb",
                user=el.get("user"),
                port=el.get("port"),
                host=device.manageIp,
                passwd=el.get("passwd"),
                cursorclass=cursors.DictCursor
            )

            res = {}
            res["id"] = "{0}_{1}".format(el.get("user"), el.get("port"))
            for key, query in self.queries.iteritems():
                try:
                    res[key] = yield dbpool.runQuery(query)
                except Exception, e:
                    self.is_clear_run = False
                    res[key] = ()
                    msg, severity = self._error(
                        str(e), el.get("user"), el.get("port"))

                    log.error(msg)
                    self._send_event("Clear", device.id, 0)
                    self._send_event(msg, device.id, severity) # Error

                    if severity == 5:
                        return

            dbpool.close()
            result.append(res)

        defer.returnValue(result)

    def process(self, device, results, log):
        log.info(
            'Modeler %s processing data for device %s',
            self.name(), device.id
        )

        maps = collections.OrderedDict([
            ('servers', []),
            ('databases', []),
        ])

        # List of servers
        server_oms = []
        for server in results:
            s_om = ObjectMap(server.get("server_size")[0])
            s_om.id = self.prepId(server["id"])
            s_om.title = server["id"]
            s_om.percent_full_table_scans = self._table_scans(
                server.get('server', ''))
            s_om.master_status = self._master_status(server.get('master', ''))
            s_om.slave_status = self._slave_status(server.get('slave', ''))
            server_oms.append(s_om)

            # List of databases
            db_oms = []
            for db in server['db']:
                d_om = ObjectMap(db)
                d_om.id = s_om.id + NAME_SPLITTER + self.prepId(db['title'])
                db_oms.append(d_om)

            maps['databases'].append(RelationshipMap(
                compname='mysql_servers/%s' % s_om.id,
                relname='databases',
                modname=MODULE_NAME['MySQLDatabase'],
                objmaps=db_oms))

        maps['servers'].append(RelationshipMap(
            relname='mysql_servers',
            modname=MODULE_NAME['MySQLServer'],
            objmaps=server_oms))

        if self.is_clear_run:
            self._send_event("Clear", device.id, 0)

        log.info(
            'Modeler %s finished processing data for device %s',
            self.name(), device.id
        )

        return list(chain.from_iterable(maps.itervalues()))

    def _error(self, error, user, port):
        """
        Create an error messsage for event.

        @param error: mysql error
        @type error: string
        @param user: user
        @type user: string
        @param port: port
        @type port: string
        @return: message and severity for event
        @rtype: str, int
        """
        if "privilege" in error:
            msg = "Access denied for user '%s', some queries failed.\
                Please check permissions" % user
            severity = 4
        elif "Access denied" in error:
            msg = "Access denied for user '%s:***:%s'. " % (user, port)
            severity = 5
        else:
            msg = "Error modelling MySQL server for "
            "%s:***:%s" % (user, port)
            severity = 5

        return msg, severity

    def _table_scans(self, server_result):
        """
        Calculates the percent of full table scans for server.

        @param server_result: result of SERVER_QUERY
        @type server_result: string
        @return: rounded value with percent sign
        @rtype: str
        """

        result = dict((el['variable_name'], el['variable_value'])
                      for el in server_result)

        if int(result['HANDLER_READ_KEY']) == 0:
            return "N/A"

        percent = float(result['HANDLER_READ_FIRST']) /\
            float(result['HANDLER_READ_KEY'])

        return str(round(percent, 3)*100)+'%'

    def _master_status(self, master_result):
        """
        Parse the result of MASTER_QUERY.

        @param master_result: result of MASTER_QUERY
        @type master_result: string
        @return: master status
        @rtype: str
        """

        if master_result:
            master = master_result[0]
            return "ON; File: %s; Position: %s" % (
                master['File'], master['Position'])
        else:
            return "OFF"

    def _slave_status(self, slave_result):
        """
        Parse the result of SLAVE_QUERY.

        @param master_result: result of SLAVE_QUERY
        @type master_result: string
        @return: slave status
        @rtype: str
        """

        if slave_result:
            slave = slave_result[0]
            return "IO running: %s; SQL running: %s; Seconds behind: %s" % (
                slave['Slave_IO_Running'], slave['Slave_SQL_Running'],
                slave['Seconds_Behind_Master'])
        else:
            return "OFF"

    def _send_event(self, reason, id, severity):
        """
        Send event for device with specified id, severity and
        error message.
        """
        self._eventService.sendEvent(dict(
            summary=reason,
            eventClass='/Status',
            device=id,
            eventKey='ConnectionError',
            severity=severity,
            ))
