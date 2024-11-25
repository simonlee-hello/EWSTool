import argparse
import os
import re
import sys
import logging
import time
from string import Template
import xml.etree.ElementTree as ET
from base64 import b64decode
import requests
from datetime import datetime
from requests_ntlm import HttpNtlmAuth
from urllib3.exceptions import InsecureRequestWarning

# 禁用SSL警告
requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

# 初始化日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 全局变量
TEMPLATES_FOLDER = "ews_post_template"
HTTP_PROTO = "https"
EXCHANGE_NAMESPACE = {
    'm': 'http://schemas.microsoft.com/exchange/services/2006/messages',
    't': 'http://schemas.microsoft.com/exchange/services/2006/types'
}
HEADERS = {
    "Content-Type": "text/xml",
    "User-Agent": "Mozilla/5.0 (Windows NT 6.3; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0.4044.129 Safari/537.36"
}

def escape(text):
    """对字符串进行XML特殊字符转义"""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\"", "&quot;"))

def escape_filename(text):
    """处理文件名中的特殊字符"""
    return re.sub(r'[<>:"/\\|?*]', '-', text)

def load_template(template_file, **kwargs):
    """加载并填充模板"""
    try:
        with open(template_file, 'r') as file:
            template = Template(file.read())
            return template.substitute(kwargs)
    except IOError as e:
        logging.error(f"无法读取模板文件: {template_file}")
        raise e


def send_soap_request(session, host, soap_body, retries=3, delay=2):
    """发送SOAP请求并处理401错误"""
    url = f"{HTTP_PROTO}://{host}/ews/exchange.asmx"

    for attempt in range(retries):
        response = session.post(url, data=soap_body, headers=HEADERS, verify=False)

        if response.status_code == 200 and "NoError" in response.text:
            return response

        if response.status_code == 401:
            logging.warning(f"认证失败，重试 {attempt + 1}/{retries} 次")
            session.auth = HttpNtlmAuth(session.auth.username, session.auth.password)
            time.sleep(delay)  # 在重试前等待一段时间

        else:
            logging.error(f"请求失败: {response.status_code} - {response.text}")
            break

    logging.error(f"认证失败，已达到最大重试次数 ({retries})")
    sys.exit(1)

def save_email_to_file(path, content):
    """保存邮件到文件"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as file:
            file.write(b64decode(content))
        logging.info(f"邮件保存成功: {path}")
    except IOError as e:
        logging.error(f"无法保存邮件: {path}")
        raise e

def find_all_people(session, host, query):
    """查找所有人员"""
    template_file = os.path.join(TEMPLATES_FOLDER, "FindAllPeople.xml")
    soap_body = load_template(template_file, string=query)
    response = send_soap_request(session, host, soap_body)

    addresses = set(re.findall(r"<Address>(.*?)</Address>", response.text))
    return [re.search(r"<EmailAddress>(.*?)</EmailAddress>", addr).group(1) for addr in addresses if '<EmailAddress>' in addr]

def download_email(session, host, folder, save_path):
    """下载指定文件夹中的邮件"""
    template_file = os.path.join(TEMPLATES_FOLDER, "ListMailOfFolder.xml")
    soap_body = load_template(template_file, folder=folder, size="50", offset="0")
    response = send_soap_request(session, host, soap_body)

    items = ET.fromstring(response.content).findall(".//t:ItemId", EXCHANGE_NAMESPACE)
    for item in items:
        email_id = item.get("Id")
        change_key = item.get("ChangeKey")
        save_single_email(session, host, email_id, change_key, save_path)

def save_single_email(session, host, email_id, change_key, save_path):
    """保存单封邮件"""
    template_file = os.path.join(TEMPLATES_FOLDER, "GetItem.xml")
    soap_body = load_template(template_file, Id=email_id, ChangeKey=change_key)
    response = send_soap_request(session, host, soap_body)

    mime_content = ET.fromstring(response.content).find(".//t:MimeContent", EXCHANGE_NAMESPACE).text
    email_file = os.path.join(save_path, escape_filename(email_id[-16:] + ".eml"))
    save_email_to_file(email_file, mime_content)

def search_emails(session, host, folder_path, type, keyword, start, end, save_path):
    """按关键字搜索邮件"""
    template_file = os.path.join(TEMPLATES_FOLDER, "SearchMail.xml")
    if type == "keyword" and keyword:
        search_condition = f"""
                <t:Or>
                  <t:Contains ContainmentMode="Substring" ContainmentComparison="IgnoreCase">
                    <t:FieldURI FieldURI="item:Subject" />
                    <t:Constant Value="{escape(keyword)}" />
                  </t:Contains>
                  <t:Contains ContainmentMode="Substring" ContainmentComparison="IgnoreCase">
                    <t:FieldURI FieldURI="item:Body" />
                    <t:Constant Value="{escape(keyword)}" />
                  </t:Contains>
                </t:Or>
            """
    elif type == "DateTimeReceived" and start and end:
        search_condition = f"""
                <t:And>
                  <t:IsGreaterThanOrEqualTo>
                      <t:FieldURI FieldURI="item:DateTimeReceived" />
                      <t:FieldURIOrConstant>
                        <t:Constant Value="{start}" />
                      </t:FieldURIOrConstant>
                    </t:IsGreaterThanOrEqualTo>
                  <t:IsLessThanOrEqualTo>
                      <t:FieldURI FieldURI="item:DateTimeReceived" />
                      <t:FieldURIOrConstant>
                        <t:Constant Value="{end}" />
                      </t:FieldURIOrConstant>
                  </t:IsLessThanOrEqualTo>
                </t:And>
            """
    elif type == "DateTimeSent" and start and end:
        search_condition = f"""
                <t:And>
                  <t:IsGreaterThanOrEqualTo>
                      <t:FieldURI FieldURI="item:DateTimeSent" />
                      <t:FieldURIOrConstant>
                        <t:Constant Value="{start}" />
                      </t:FieldURIOrConstant>
                    </t:IsGreaterThanOrEqualTo>
                  <t:IsLessThanOrEqualTo>
                      <t:FieldURI FieldURI="item:DateTimeSent" />
                      <t:FieldURIOrConstant>
                        <t:Constant Value="{end}" />
                      </t:FieldURIOrConstant>
                  </t:IsLessThanOrEqualTo>
                </t:And>
            """
    else:
        raise ValueError("Invalid type or missing date range for 'date' search")

    soap_body = load_template(template_file, folderpath=folder_path, search_condition=search_condition, max_count=100, offset=0)
    response = send_soap_request(session, host, soap_body)

    root = ET.fromstring(response.content)
    items = root.findall(".//t:ItemId", EXCHANGE_NAMESPACE)
    for item in items:
        save_single_email(session, host, item.get('Id'), item.get('ChangeKey'), save_path)

def main():
    parser = argparse.ArgumentParser(description="EWS工具")
    parser.add_argument("--host", required=True, help="目标Exchange服务器")
    # parser.add_argument("--mode", required=True, choices=["plaintext", "hash"], help="认证模式")
    parser.add_argument("--username", required=True, help="用户名")
    parser.add_argument("--password", required=False, help="明文密码")
    parser.add_argument("--hash", required=False, help="哈希")
    # parser.add_argument("--command", required=True, choices=["download", "people", "search"], help="命令")
    parser.add_argument("--proxy", help="HTTP代理")

    # command to get all people`s email
    parser.add_argument("--people", required=False, help="获取所有人员的邮箱地址")
    # command to download emails
    # parser.add_argument("--download", required=False, help="下载指定文件夹的邮箱")
    parser.add_argument("--download", default="inbox", required=False, choices=["inbox", "sentitems"], help="下载指定邮箱文件夹的邮件")

    # New arguments for search command
    parser.add_argument("--search", choices=["keyword", "DateTimeReceived", "DateTimeSent"], help="搜索类型")
    parser.add_argument("--keyword", help="搜索关键词")
    parser.add_argument("--start", help="搜索起始日期")
    parser.add_argument("--end",default=datetime.today().strftime('%Y-%m-%d'), help="搜索截止日期")

    args = parser.parse_args()

    # password = args.password.upper() if args.mode == "hash" else args.password
    if args.password:
        password_or_hash = args.password
    elif args.hash:
        password_or_hash = "00000000000000000000000000000000:" + args.hash.upper()
    else:
        logging.error("请输入密码或哈希")
        sys.exit(1)
    proxies = {"http": args.proxy, "https": args.proxy} if args.proxy else None

    session = requests.Session()
    session.auth = HttpNtlmAuth(args.username, password_or_hash)
    session.proxies = proxies

    if args.people:
        logging.info("查找所有人员")
        all_results = set()

        # 遍历 a-z 进行查找
        for char in "abcdefghijklmnopqrstuvwxyz":
            logging.info(f"正在查找: {char}")
            results = find_all_people(session, args.host, char)
            all_results.update(results)

        # 将结果写入文件
        with open("emails.txt", "w", encoding="utf-8") as file:
            # 计算地址的数量
            total_count = len(all_results)
            for addr in sorted(all_results):
                file.write(addr + "\n")
                logging.info(addr)
            # 打印总数量
            logging.info(f"共计 {total_count} 个结果已写入文件 emails.txt")
    elif args.download:
        save_path = os.path.join(os.getcwd(), args.username, args.download)
        download_email(session, args.host, args.download, save_path)
    elif args.search:
        if args.search == "keyword" and not args.keyword:
            logging.error("必须指定搜索关键词（--keyword）")
            sys.exit(1)

        if (args.search == "DateTimeReceived" or args.search == "DateTimeSent") and not args.start and not args.end:
            logging.error("必须指定起始日期（--start）或截止日期（--end）")
            sys.exit(1)

        # Prepare search parameters

        save_path = os.path.join(os.getcwd(), args.username)
        if args.search == "keyword":
            save_path = os.path.join(save_path, f"Search-{escape_filename(args.keyword)}")
            logging.info(f"开始搜索标题和正文中包含 {args.keyword} 关键字的邮件")
        elif args.search == "DateTimeReceived" or args.search == "DateTimeSent":
            save_path = os.path.join(save_path,
                                     f"Search-{args.search}-From-{escape_filename(args.start)}-To-{escape_filename(args.end)}")
            logging.info(f"开始搜索日期从 {args.start} 到 {args.end} 的邮件")

        for folder in ['inbox', 'sentitems']:
            search_emails(session, args.host, folder, args.search, args.keyword, args.start, args.end,
                          os.path.join(save_path, folder))

if __name__ == "__main__":
    main()
