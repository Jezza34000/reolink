# This file is part of Jeedom.
#
# Jeedom is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Jeedom is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Jeedom. If not, see <http://www.gnu.org/licenses/>.
#


import logging
import threading
import requests
import datetime
import collections
import os
from queue import Queue
import socketserver
from socketserver import (TCPServer, StreamRequestHandler)


# ------------------------------------------------------------------------------

class jeedom_com:
    def __init__(self, apikey='', url='', cycle=0.5, retry=3):
        self.apikey = apikey
        self.url = url
        self.cycle = cycle
        self.retry = retry
        self.changes = {}
        if cycle > 0:
            self.send_changes_async()
        logging.debug('Init request module v%s' % (str(requests.__version__),))
    
    def send_changes_async(self):
        try:
            if len(self.changes) == 0:
                resend_changes = threading.Timer(self.cycle, self.send_changes_async)
                resend_changes.start()
                return
            start_time = datetime.datetime.now()
            changes = self.changes
            self.changes = {}
            logging.debug('Send to jeedom : ' + str(changes))
            i = 0
            while i < self.retry:
                try:
                    r = requests.post(self.url + '?apikey=' + self.apikey, json=changes, timeout=(0.5, 120),
                                      verify=False)
                    if r.status_code == requests.codes.ok:
                        break
                except Exception as error:
                    logging.error(
                        'Error on send request to jeedom ' + str(error) + ' retry : ' + str(i) + '/' + str(self.retry))
                i = i + 1
            if r.status_code != requests.codes.ok:
                logging.error('Error on send request to jeedom, return code %s' % (str(r.status_code),))
            dt = datetime.datetime.now() - start_time
            ms = (dt.days * 24 * 60 * 60 + dt.seconds) * 1000 + dt.microseconds / 1000.0
            timer_duration = self.cycle - ms
            if timer_duration < 0.1:
                timer_duration = 0.1
            if timer_duration > self.cycle:
                timer_duration = self.cycle
            resend_changes = threading.Timer(timer_duration, self.send_changes_async)
            resend_changes.start()
        except Exception as error:
            logging.error('Critical error on  send_changes_async %s' % (str(error),))
            resend_changes = threading.Timer(self.cycle, self.send_changes_async)
            resend_changes.start()
    
    def add_changes(self, key, value):
        if key.find('::') != -1:
            tmp_changes = {}
            changes = value
            for k in reversed(key.split('::')):
                if k not in tmp_changes:
                    tmp_changes[k] = {}
                tmp_changes[k] = changes
                changes = tmp_changes
                tmp_changes = {}
            if self.cycle <= 0:
                self.send_change_immediate(changes)
            else:
                self.merge_dict(self.changes, changes)
        else:
            if self.cycle <= 0:
                self.send_change_immediate({key: value})
            else:
                self.changes[key] = value
    
    def send_change_immediate(self, change):
        threading.Thread(target=self.thread_change, args=(change,)).start()
    
    def thread_change(self, change):
        logging.debug('Send to jeedom :  %s' % (str(change),))
        i = 0
        while i < self.retry:
            try:
                r = requests.post(self.url + '?apikey=' + self.apikey, json=change, timeout=(0.5, 120), verify=False)
                if r.status_code == requests.codes.ok:
                    break
            except Exception as error:
                logging.error(
                    'Error on send request to jeedom ' + str(error) + ' retry : ' + str(i) + '/' + str(self.retry))
            i = i + 1
    
    def set_change(self, changes):
        self.changes = changes
    
    def get_change(self):
        return self.changes
    
    def merge_dict(self, d1, d2):
        for k, v2 in d2.items():
            v1 = d1.get(k)  # returns None if v1 has no value for this key
            if (isinstance(v1, collections.Mapping) and
                    isinstance(v2, collections.Mapping)):
                self.merge_dict(v1, v2)
            else:
                d1[k] = v2
    
    def test(self):
        try:
            response = requests.get(self.url + '?apikey=' + self.apikey, verify=False)
            if response.status_code != requests.codes.ok:
                logging.error('Callback error: %s %s. Please check your network configuration page'
                              % (response.status_code, response.reason,))
                logging.error(response.text)
                return False
        except Exception as e:
            logging.error(
                'Callback result as a unknown error: %s. Please check your network configuration page' % (e.message,))
            return False
        return True


# ------------------------------------------------------------------------------

class jeedom_utils:
    
    @staticmethod
    def convert_log_level(level='error'):
        log_lvl = {'debug': logging.DEBUG,
                   'info': logging.INFO,
                   'notice': logging.WARNING,
                   'warning': logging.WARNING,
                   'error': logging.ERROR,
                   'critical': logging.CRITICAL,
                   'none': logging.CRITICAL}
        return log_lvl.get(level, logging.CRITICAL)
    
    @staticmethod
    def set_log_level(level='error'):
        log_format = '[%(asctime)-15s][%(levelname)s] : %(message)s'
        logging.basicConfig(level=jeedom_utils.convert_log_level(level), format=log_format, datefmt="%Y-%m-%d %H:%M:%S")
    
    @staticmethod
    def write_pid(path):
        pid = str(os.getpid())
        logging.debug("Writing PID " + pid + " to " + str(path))
        open(path, 'w').write("%s\n" % pid)


# ------------------------------------------------------------------------------

JEEDOM_SOCKET_MESSAGE = Queue()


class JeedomSocketHandler(StreamRequestHandler):
    def handle(self):
        global JEEDOM_SOCKET_MESSAGE
        logging.debug("Client connected to [%s:%d]" % self.client_address)
        lg = self.rfile.readline()
        JEEDOM_SOCKET_MESSAGE.put(lg)
        # logging.debug("Message read from socket: " + str(lg.strip()))
        self.netAdapterClientConnected = False
        logging.debug("Client disconnected from [%s:%d]" % self.client_address)


class jeedom_socket():

    def __init__(self, address='localhost', port=55000):
        self.address = address
        self.port = port
        socketserver.TCPServer.allow_reuse_address = True

    def open(self):
        self.netAdapter = TCPServer((self.address, self.port), JeedomSocketHandler)
        if self.netAdapter:
            logging.debug("Socket interface started")
            threading.Thread(target=self.loopNetServer, args=()).start()
        else:
            logging.debug("Cannot start socket interface")

    def loopNetServer(self):
        logging.debug("LoopNetServer Thread started")
        logging.debug("Listening on: [%s:%d]" % (self.address, self.port))
        self.netAdapter.serve_forever()
        logging.debug("LoopNetServer Thread stopped")

    def close(self):
        self.netAdapter.shutdown()

    def getMessage(self):
        return self.message

# ------------------------------------------------------------------------------
# END
# ------------------------------------------------------------------------------
