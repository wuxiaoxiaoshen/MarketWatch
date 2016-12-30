#!/usr/bin/env python
# encoding: utf-8

"""
@author: paul.xie
@software: PyCharm
@time: 2016/12/28 13:02
"""
import datetime

import pymongo
import requests
import re
import pyquery
from conf.config import HOST, DB, PORT, COLLECTION, COLL_BASE, COLL_ITEMS, COLL_VALUES, COLL
from lxml import etree
from conf.config import marketwatch_config
from pymongo import MongoClient
from pymongo import errors

class MarketCrawler(object):
    category = "marketwatch"

    def __init__(self):
        # db setting
        self.collection = MongoClient(HOST, PORT)[DB][COLLECTION]
        self.coll_base = MongoClient(HOST, PORT)[DB][COLL_BASE]
        self.coll_items = MongoClient(HOST, PORT)[DB][COLL_ITEMS]
        self.coll_values = MongoClient(HOST, PORT)[DB][COLL_VALUES]
        self.coll = MongoClient(HOST, PORT)[DB][COLL]

        # Field
        self.field = ["Name", "Market", "Ticker", ]

        # headers
        self.headers = marketwatch_config["headers"]

        # Field pattern
        self.instrumentheader = "//div[@id='instrumentheader']"
        self.table = '//div[@class="financials"]/table[@class="crDataTable"]'    # 有返回值, 内容存在;
        self.partialsum = self.table + "[@class='partialSum']"
        self.childrow = self.table + "[@class='childRow']"
        self.mainrow = self.table + "[@class='mainRow']"
        self.rowlevel_2 = self.table + "[@class='rowLevel-2']"
        self.rowlevel_3 = self.table + "[@class='rowLevel-3']"
        self.totalrow = self.table + "[@class='totalRow']"

        self.pattern = [self.partialsum, self.childrow, self.mainrow, self.rowlevel_2, self.rowlevel_3, self.totalrow]

    def get_tick_from_db(self):
        coll = self.collection
        ticks = coll.find({"code": {"$in": [re.compile("_NY_EQ"), re.compile("_NQ_EQ")]}}, {"tick": 1, "_id": 0})
        code_tick = [one["tick"] for one in ticks]
        return code_tick

    def get_url(self, tick="FISV", keyword="INCOME_ANNUAL_URL"):
        """
        comment by paul.xie
        :param tick:
        :param keyword:
        :return:
        """
        url = marketwatch_config[keyword].format(tick)
        return url

    def download(self, url):
        """
        comment by paul.xie
        :return:
        """
        try:
            raw_html = requests.get(url, headers=self.headers)
            response = raw_html.text
            return response
        except requests.ConnectionError:
            pass

    def split_string(self, raw_string):
        """
        comment by paul.xie
        :param raw_string:
        :return:
        1. 处理字符串函数，分割操作
        """
        new_string_list = raw_string.split(":")
        first, last = new_string_list[0].strip(), new_string_list[1].strip()
        return (first, last)

    def pattern_string(self, raw_string, flag=False):
        new_string_list = raw_string.split(' ')
        if not flag:
            fy = new_string_list[3]
            currency = new_string_list[6]
            unit = new_string_list[7]
        else:
            fy = None
            currency = new_string_list[2]
            unit = new_string_list[3]
        return currency, unit, fy

    def replace_column_field(self, pattern, new_word):
        """
        comment by paul.xie
        :return:
         1. 对抓取到的字段进行替换操作
        """
        length_pattern = len(pattern)
        count = 0
        for one in range(length_pattern):
            if pattern[one]:
                pattern[one] = pattern[one].strip("\r").strip("\t").strip()
            if pattern[one] is None or pattern[one] == "":
                pattern[one] = new_word[count]
                count += 1
            if count > len(new_word):
                break
        return pattern

    def clean_column_field(self, pattern_list):
        length = len(pattern_list)
        for one in range(length):
            if len(pattern_list[one])!= 1:
                for i in pattern_list[one]:
                    i = i.strip("\r").strip("\n").strip()
                    if i:
                        pattern_list[one] = [i]

    def clean_data_field(self, pattern_dict):
        """
        comment by paul.xie
        :param pattern_list: 一张表的数据, 包括6列，数值在第2到6列
        :return:
        1. 清洗数值：(19M) 为负数；"-": 为 0
        2. 转成列表
        """
        length = len(pattern_dict)+1
        new_pattern_dict = {}
        new_pattern_dict["column1"] = [one[0] for one in pattern_dict["column1"]]
        for one in range(2, length, 1):
            old_pattern = pattern_dict['column{}'.format(one)]    # 列
            length_old_pattern = len(old_pattern)
            new_one_list = []
            for each in range(length_old_pattern):
                field_content = str(old_pattern[each][0])    # 单元格
                if "(" in field_content:
                    old_pattern[each][0] = str("-" + field_content[1:-1])
                if field_content == "-":
                    old_pattern[each][0] = str(0)
                new_one_list.append(old_pattern[each][0])
            new_pattern_dict["column{}".format(one)] = new_one_list
        return new_pattern_dict

    def parse_raw_html(self, response):
        """
        comment by paul.xie
        :return:
        """
        if response:
            selector = etree.HTML(response)

            # 公司代码和名称
            market_and_ticker = selector.xpath(self.instrumentheader)[0]
            name = market_and_ticker.xpath("h1")[0].text
            info = market_and_ticker.xpath('p')[0].text
            market, ticker = self.split_string(info)
            base_info = {
                "name": name,
                "market": market,
                "ticker": ticker,
                "key": ticker,
                "ct": datetime.datetime.now()
            }
            #print (base_info)
            try:
                self.coll_base.insert(base_info)
            except errors.DuplicateKeyError:
                pass
            # 表格信息抓取
            tables_info = selector.xpath(self.table)
            for table in tables_info:    # 表格

                # 获取表格title信息，包括财年，货币类型，货币单位
                title = table.xpath('thead/tr[@class]/th[@class="rowTitle"]')
                for one in title:
                    if one.text:
                        currency, unit, fy = self.pattern_string(one.text, flag=False)    # 季度无fy字段
                        #print (currency, unit, fy)

                years_scope = table.xpath("thead//th[@scope][position()<6]")
                years = [year.text.strip() for year in years_scope]

                # 获取科目样式：年度数据  |self.coll_items  获取财务数据： |self.coll_vaules
                column_one = table.xpath("tbody/tr/td[@class='rowTitle']/a")
                ones_tail = [one.tail.strip("\r").strip("\n").strip() for one in column_one]    # 第一列缺失字段
                column_item = {}  # 一张表格的数据
                for length in range(1, 7, 1):
                    trs = table.xpath("tbody/tr/td[{}]".format(length))
                    column_item["column{}".format(length)] = [one.xpath('text()|span/text()') for one in trs]
                self.clean_column_field(column_item["column1"])
                column_class_list = table.xpath("tbody/tr/@class")
                column_item = self.clean_data_field(column_item)
                # try:
                #     self.coll_base.insert({"name": column_class_list})
                # except errors.DuplicateKeyError:
                #     pass
                # try:
                #     self.coll.insert(column_item)
                # except errors.DuplicateKeyError:
                #     pass
                # 样式字段
                item_info = {
                    

                }
                # 值字段
                values_info = {


                }

    def ticker_flag(self, url):
        """
        comment by paul.xie
        :return:
        1. ticker 股票不存在，网页跳转
        """
        flag = True
        raw_html = requests.get(url, headers=self.headers)
        if url != raw_html.url:
            flag = False
        return flag

    def table_flag(self, response):
        """
        comment by paul.xie
        :return:
        1. ticker 股票存在但没数据
        """
        flag = False
        selector = etree.HTML(response)
        table_content = selector.xpath(self.table)
        if table_content:
            flag = True
        return flag


if __name__ == "__main__":
    marketwatch = MarketCrawler()
    url = marketwatch.get_url("FISV", "INCOME_ANNUAL_URL")
    #url = marketwatch.get_url("FISV", "INCOME_QUARTER_URL")
    print(url)
    content = marketwatch.download(url)
    tr_content = marketwatch.parse_raw_html(content)
    print tr_content

