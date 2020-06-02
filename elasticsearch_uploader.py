###################################################################################################
# Elastic Search Uploader
# Date: 05/26/2020
# Version: 1.1
# Author: Yixing Qiu (yixqiu)

import concurrent.futures
import traceback
import copy
import logging
import threading
import datetime
from time import sleep
from elasticsearch import Elasticsearch, helpers, ElasticsearchException
from urllib3.exceptions import ReadTimeoutError
from typing import List, Set, Dict, Union

class ElasticSearchUploader(threading.Thread):
    def __init__(self, elasticsearch_ip, port, bulksize, index, *args, **kwargs):
        threading.Thread.__init__(self)
        self.elasticsearch_ip = elasticsearch_ip
        self.port = port
        self.bulksize = bulksize
        self.index = index
        self.data_list = []
        self.log = kwargs.get('log', None)
        if self.log is None:
            # init logger
            formatting = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            logger = logging.basicConfig(format=formatting, level=logging.INFO)
            self.log = logging.getLogger('ElasticSearchUploader Log')

    def run(self):
        es = Elasticsearch([{'host': self.elasticsearch_ip, 'port': self.port}], timeout=600)
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

            sleep(10)  # check every 10 seconds



