###################################################################################################
# SSH Elastic Search Subscription
# Date: 05/26/2020
# Version: 1.1
# Author: Yixing Qiu (yixqiu)

import json
import argparse
import glob
import time
import sys
import concurrent.futures
import traceback
import copy
import ipaddress
import logging
import threading
import datetime
import paramiko
from netmiko import ConnectHandler
from time import sleep
from elasticsearch import Elasticsearch, helpers, ElasticsearchException
from urllib3.exceptions import ReadTimeoutError
from typing import List, Set, Dict, Union

class SSHConnection(threading.Thread):
    def __init__(self, host, log, lock, username, password, duration, cmd_list, elastic, output, hostname,
                 sshElasticSearchUploader):
        threading.Thread.__init__(self)
        self.host = host
        self.username = username
        self.password = password
        self.lock = lock
        self.log = log
        self.duration = duration
        self.cmd_list = cmd_list
        self.elastic = elastic
        self.output = output
        self.hostname = hostname
        self.sshElasticSearchUploader = sshElasticSearchUploader

    def run(self):
        logging.getLogger('paramiko').setLevel(logging.WARNING)
        with self.lock:
            #            self.log.info('%s is acquiring lock, creating an ssh session to %s' % (threading.current_thread().name, self.host))
            err_logname = 'error-' + self.host
            runtime_logname = 'runtime-' + self.host + '.csv'
            try:
                with ConnectHandler(ip=self.host,
                                    port=22,
                                    username=self.username,
                                    password=self.password,
                                    device_type="cisco_ios",
                                    timeout=120,
                                    global_delay_factor=5) as ch:

                    cmds_start = datetime.datetime.now()
                    time_stamp_start = cmds_start.strftime('%Y-%m-%d %H:%M:%S')
                    #                    time_stamp_start = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    print("(" + time_stamp_start + ") " + threading.current_thread().name + " start")

                    # Send cmds to device
                    cmd_result = ""
                    for cmd in self.cmd_list:
                        cmd_result = cmd_result + ch.send_command(cmd)

                        # Print the raw command output to the screen
                    #                        print(result)

                    cmds_end = datetime.datetime.now()
                    time_stamp_cmds_end = cmds_end.strftime('%Y-%m-%d %H:%M:%S')

                    # count number of ssh session
                    show_ssh_result = ch.send_command("show ssh")
                    show_ssh_result = show_ssh_result[show_ssh_result.find('Incoming sessions'):show_ssh_result.find(
                        'Outgoing sessions')]
                    ssh_session_count = show_ssh_result.count('\n') - 2

                    # Sleep
                    time.sleep(self.duration)

                    time_stamp_session_end = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    print("(" + time_stamp_session_end + ") " + threading.current_thread().name + " end")

                    # Put data in a dict
                    data = {}
                    data['@timestamp'] = cmds_start.timestamp() * 1000
                    #                    data['@timestamp'] = cmds_start
                    data['hostname'] = self.hostname
                    data['ssh_thread_id'] = threading.current_thread().ident
                    data['ssh_thread_name'] = threading.current_thread().name
                    data['ssh_time_start'] = time_stamp_start
                    data['ssh_time_end'] = time_stamp_cmds_end
                    data['ssh_runtime'] = (cmds_end - cmds_start) / datetime.timedelta(seconds=1)
                    data['ssh_session_count'] = ssh_session_count
                    data['ssh_result_size'] = sys.getsizeof(cmd_result)

                    # Put dict in the ElasticSearch upload list
                    self.sshElasticSearchUploader.data_list.append(data)

                # Output cmd execution time to a .csv file
                if self.output:
                    with open(runtime_logname, 'a') as rl:
                        rl.write(str(data['ssh_thread_id'])
                                 + "," + data['ssh_thread_name']
                                 + "," + data['ssh_time_start']
                                 + "," + data['ssh_time_end']
                                 + "," + str(data['ssh_runtime'])
                                 + "," + str(data['ssh_session_count'])
                                 + "," + str(data['ssh_result_size'])
                                 + "\n")

            except paramiko.SSHException as e:
                with open(err_logname, 'a') as f:
                    time_stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    f.write(
                        'Error (' + time_stamp + '): ' + self.host + '-' + threading.current_thread().name + ': ' + str(
                            e) + '\n')
            #                    self.log.error('%s %s' %(threading.current_thread().name, e))
            except EOFError:
                with open(err_logname, 'a') as f:
                    time_stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    f.write(
                        'Error (' + time_stamp + '): ' + self.host + '-' + threading.current_thread().name + ': EOFError occured\n')


#                self.log.error('%s %s' %(threading.current_thread().name, 'EOFError occured'))
#            finally:
#                self.log.info('%s is releasing lock, closing ssh session to %s' % (threading.current_thread().name, self.host))
#                self.log.info('%s ended succesfully' % (threading.current_thread().#name#))

class SSHElasticSearchUploader(threading.Thread):
    def __init__(self, bulksize, index, log):
        threading.Thread.__init__(self)
        self.bulksize = bulksize
        self.index = index
        self.log = log
        self.data_list = []

    def run(self):
        es = Elasticsearch([{'host': '2.2.2.1', 'port': 9200}], timeout=600)
        while True:
            self.log.info("Size of the data list: " + str(len(self.data_list)))

            # Upload data to Elastic Search when data_list size passes bulksize
            if len(self.data_list) >= self.bulksize:

                try:
                    # Check if the index already exists. If not, initialize one
                    index_name = self.index + '-' + datetime.datetime.now().strftime('%Y.%m.%d')
                    if not es.indices.exists(index=index_name):
                        self.log.info("Index not exists. Creating one.")
                        request_body = {
                            "settings": {
                                "index": {
                                    "max_docvalue_fields_search": "1000"
                                }
                            },
                            'mappings': {
                                '_doc': {
                                    'properties': {
                                        '@timestamp': {'type': 'date'}
                                    }
                                }
                            }
                        }
                        es.indices.create(index=index_name, body=request_body, include_type_name=True)
                        self.log.info("New index is created: " + index_name)

                    # Upload the data list to Elastic Search
                    self.log.info("Uploading the data list to Elastic Search")
                    data_list_tmp = self.data_list.copy()
                    self.data_list.clear()
                    #                print(data_list_tmp)
                    helpers.bulk(es, data_list_tmp, index=index_name, doc_type='_doc')
                    self.log.info("Upload done")

                except ElasticsearchException as e:
                    self.log.error(e)
                except ReadTimeoutError as e:
                    self.log("read timeout error")
                    self.log.error(e)

            time.sleep(10)  # check every 10 seconds


class SSHTestcase(object):
    def __init__(self, username, password, host, semaphore, duration, interval, cmd_list, elastic, output, hostname,
                 log, sshElasticSearchUploader):
        self.user = username
        self.password = password
        self.host = host
        self.int_sem = int(semaphore)
        self.sem = threading.Semaphore(self.int_sem)
        self.duration = float(duration)
        self.interval = float(interval)
        self.cmd_list = cmd_list
        self.elastic = elastic
        self.output = output
        self.threads = []
        self.hostname = hostname
        self.log = log
        self.sshElasticSearchUploader = sshElasticSearchUploader

    def run_testcase(self):
        while True:
            for _ in range(self.int_sem):
                self.threads = []
                self.threads.append((SSHConnection(self.host, self.log, self.sem, self.user, self.password,
                                                   self.duration, self.cmd_list, self.elastic, self.output,
                                                   self.hostname, self.sshElasticSearchUploader)))
            for thread in self.threads:
                thread.start()
                time.sleep(self.interval)

    #            main_thread = threading.currentThread()
    #            for t in threading.enumerate():
    #                if t is main_thread:
    #                     continue
    #                t.join()

    def _init_logger(self):
        formatting = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        logger = logging.basicConfig(format=formatting, level=logging.INFO)
        log = logging.getLogger('SSH Stress')
        return log

def main():
    ############################# INITIALIZATION #############################
    #### Argparse block ####

    parser = argparse.ArgumentParser(description='SSH Stress script')
    parser.add_argument("-u", "--user", dest="username", default="root")
    parser.add_argument("-p", "--password", dest="password", default="lablab", help="password")
    parser.add_argument("-a", "--host", dest="host", help="Host IP address", required=True)
    parser.add_argument("-s", "--semaphore", dest="sem", help="Number of concurent threads executing ssh connections",
                        default=1)
    parser.add_argument("-d", "--duration", dest="duration", help="Number of seconds each ssh session stays",
                        default=10)
    parser.add_argument("-i", "--interval", dest="interval",
                        help="Number of seconds between the starts of 2 ssh sessions", default=3)
    parser.add_argument("-f", "--filename", dest="filename",
                        help="Path and name of the file which contains the CLI commands. Will run show running-config if not specified",
                        default="no commands input")
    parser.add_argument('-e', "--elastic", type=str, default="no",
                        help="Upload or not to elastic search. Default is no")
    parser.add_argument("-o", "--output", dest="output", help="Output the data to a file or not. Default is no",
                        default="no")
    parser.add_argument('-b', "--bulksize", type=int, default=100, help="Bulk size of the data list")

    args = parser.parse_args()
    #### End of Argparse block ####

    cmd_list = []
    # The the command list from the file
    if args.filename == "no commands input":
        cmd_list.append("show run")
    else:
        with open(args.filename) as f:
            cmd_list = f.readlines()

    elastic = False
    if args.elastic == "yes":
        elastic = True

    output = False
    if args.output == "yes":
        output = True

    # init logger
    formatting = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    logger = logging.basicConfig(format=formatting, level=logging.INFO)
    log = logging.getLogger('SSH Subscription')

    # Get Hostname and verify ssh connection
    hostname = ''
    log.info("Verifying the connection and Getting the hostname")
    try:
        with ConnectHandler(ip=args.host,
                            port=22,
                            username=args.username,
                            password=args.password,
                            device_type="cisco_ios",
                            timeout=120,
                            global_delay_factor=5) as ch:

            cmd = "show run hostname"
            result = ch.send_command(cmd)
            hostname = result.split("hostname", 1)[1].rstrip("\n").strip()
    except paramiko.SSHException as e:
        log.error(e)
        return
    if hostname == '':
        log.error("Cannot get the hostname. Exit")
        return
    log.info("Router hostname: " + hostname)

    # Start the SSH Elastic Search Uploader
    index = 'ssh_stress'
    sshElasticSearchUploader = SSHElasticSearchUploader(args.bulksize, index, log)
    sshElasticSearchUploader.start()

    # Start SSH Sessions
    sshTestcase = SSHTestcase(args.username, args.password, args.host, args.sem, args.duration, args.interval, cmd_list,
                              elastic, output, hostname, log, sshElasticSearchUploader)
    sshTestcase.run_testcase()


if __name__ == '__main__':
    main()


