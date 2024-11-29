import argparse
import os
import re
import ssl
import sys
import logging
import time
from itertools import count
from string import Template
import xml.etree.ElementTree as ET
from base64 import b64decode
import requests
from datetime import datetime
from requests_ntlm import HttpNtlmAuth
from urllib3.exceptions import InsecureRequestWarning
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

# 禁用SSL警告
requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

# 初始化日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

class Config:
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

class TLSAdapter(HTTPAdapter):
    def __init__(self, tls_version=None, **kwargs):
        self.tls_version = tls_version
        super().__init__(**kwargs)

    def init_poolmanager(self, *args, **kwargs):
        context = create_urllib3_context()
        if self.tls_version:
            context.options |= self.tls_version
        context.check_hostname = False  # 禁用 check_hostname
        context.verify_mode = ssl.CERT_NONE  # 禁用证书验证
        kwargs['ssl_context'] = context
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        context = create_urllib3_context()
        if self.tls_version:
            context.options |= self.tls_version
        context.check_hostname = False  # 禁用 check_hostname
        context.verify_mode = ssl.CERT_NONE  # 禁用证书验证
        kwargs['ssl_context'] = context
        return super().proxy_manager_for(*args, **kwargs)

class EWSClient:
    def __init__(self, host, username, password_or_hash, proxy=None, tls_version=None):
        self.host = host
        self.username = username
        self.password_or_hash = password_or_hash
        self.proxy = proxy
        self.tls_version = tls_version
        self.session = self.create_session()

    def create_session(self):
        session = requests.Session()
        session.auth = HttpNtlmAuth(self.username, self.password_or_hash)
        if self.proxy:
            session.proxies = {"http": self.proxy, "https": self.proxy}
        if self.tls_version:
            tls_adapter = TLSAdapter(tls_version=self.tls_version)
            session.mount('https://', tls_adapter)
        return session

    def send_soap_request(self, soap_body, retries=3, delay=2):
        url = f"{Config.HTTP_PROTO}://{self.host}/ews/exchange.asmx"

        for attempt in range(retries):
            try:
                response = self.session.post(url, data=soap_body, headers=Config.HEADERS, verify=False)

                if response.status_code == 200 and "NoError" in response.text:
                    return response

                if response.status_code == 401:
                    logging.warning(f"认证失败，重试 {attempt + 1}/{retries} 次")
                    time.sleep(delay)  # 在重试前等待一段时间
                    self.session = self.create_session()  # 重新创建 session

                else:
                    logging.error(f"请求失败: {response.status_code} - {response.text}")
                    break
            except requests.RequestException as e:
                logging.error(f"请求发生异常: {e}")

        logging.error(f"请求失败，已达到最大重试次数 ({retries})")
        sys.exit(1)

    def find_all_people(self, query):
        """查找所有人员"""
        template_file = os.path.join(Config.TEMPLATES_FOLDER, "FindAllPeople.xml")
        soap_body = load_template(template_file, string=query)
        response = self.send_soap_request(soap_body)
        root = ET.fromstring(response.content)

        # 遍历所有 Persona 元素并提取人员信息
        peoples = set()
        emails = set()

        for persona in root.findall(".//t:Persona", namespaces=Config.EXCHANGE_NAMESPACE):
            display_name = persona.findtext(".//t:DisplayName", namespaces=Config.EXCHANGE_NAMESPACE)
            surname = persona.findtext(".//t:Surname", namespaces=Config.EXCHANGE_NAMESPACE)
            email_address = persona.findtext(".//t:EmailAddress/t:EmailAddress", namespaces=Config.EXCHANGE_NAMESPACE)

            if display_name is None:
                display_name = "No_DisplayName"
            if surname is None:
                surname = "No_Surname"
            if email_address is None:
                email_address = "No_EmailAddress"

            person_info = f"显示名: {display_name}, 姓氏: {surname}, 邮箱: {email_address}"
            peoples.add(person_info)
            emails.add(email_address)

        return peoples, emails

    def get_folders(self):
        template_file = os.path.join(Config.TEMPLATES_FOLDER, "ListFolder.xml")
        soap_body = load_template(template_file)
        response = self.send_soap_request(soap_body)
        root = ET.fromstring(response.content)
        folder_elements = root.findall('.//t:Folder', namespaces=Config.EXCHANGE_NAMESPACE)
        if not folder_elements:
            logging.info("没有找到文件夹。")
            return []

        folders = []
        for folder in folder_elements:
            folder_name = folder.findtext(".//t:DisplayName", namespaces=Config.EXCHANGE_NAMESPACE)
            folder_id = folder.find(".//t:FolderId", namespaces=Config.EXCHANGE_NAMESPACE).get('Id')
            folder_key = folder.find(".//t:FolderId", namespaces=Config.EXCHANGE_NAMESPACE).get('ChangeKey')
            folder_total_count = folder.findtext(".//t:TotalCount", namespaces=Config.EXCHANGE_NAMESPACE)
            folder_child_folder_count = folder.findtext(".//t:ChildFolderCount", namespaces=Config.EXCHANGE_NAMESPACE)
            folder_unread_count = folder.findtext(".//t:UnreadCount", namespaces=Config.EXCHANGE_NAMESPACE)

            folders.append({'name': folder_name, 'id': folder_id, 'key': folder_key, 'total_count': folder_total_count,
                            'child_folder_count': folder_child_folder_count, 'unread_count': folder_unread_count})
            logging.info(
                f"找到文件夹: {folder_name} 邮件数量共计: {folder_total_count} 子文件夹数量: {folder_child_folder_count} 未读数量: {folder_unread_count}")

        return folders

    def get_email_count(self, folder):
        """获取指定文件夹中的邮件总数"""
        template_file = os.path.join(Config.TEMPLATES_FOLDER, "GetSizeOfFolder.xml")
        soap_body = load_template(template_file, folder_id=folder['id'], folder_key=folder['key'])
        response = self.send_soap_request(soap_body)

        # 解析文件夹的邮件总数
        folder_item_count = ET.fromstring(response.content).find(".//t:TotalCount", Config.EXCHANGE_NAMESPACE)
        if folder_item_count is not None:
            return int(folder_item_count.text)
        else:
            logging.warning(f"无法获取文件夹 {folder} 的邮件总数，默认为 0。")
            return 0

    def download_email(self, folder, save_path):
        # Step 1: 获取文件夹中邮件的总数量
        total_count = self.get_email_count(folder)
        if total_count == 0:
            logging.info(f"文件夹 {folder['name']} 中没有邮件，无需下载。")
            return

        logging.info(f"文件夹 {folder['name']} 中有 {total_count} 封邮件，准备下载...")

        # Step 2: 分页下载邮件
        template_file = os.path.join(Config.TEMPLATES_FOLDER, "ListMailOfFolder.xml")
        size = 50  # 每页的邮件数量
        offset = 0  # 初始偏移量

        while offset < total_count:
            # 使用模板生成 SOAP 请求体
            soap_body = load_template(template_file, folder_id=folder['id'], folder_key=folder['key'], size=str(size), offset=str(offset))
            response = self.send_soap_request(soap_body)

            # 解析返回的邮件列表
            items = ET.fromstring(response.content).findall(".//t:ItemId", Config.EXCHANGE_NAMESPACE)
            if not items:
                break

            # 下载当前批次的邮件
            for item in items:
                email_id = item.get("Id")
                change_key = item.get("ChangeKey")
                self.save_single_email(email_id, change_key, save_path)

            if total_count < size:
                break
            # 增加偏移量以获取下一页
            offset += size
            logging.info(f"已下载 {offset} 封邮件，继续下一页...")

        logging.info(f"文件夹 {folder['name']} 内邮件下载完成, 共下载 {total_count} 封邮件, 所有文件保存路径：{save_path}")

    def save_single_email(self, email_id, change_key, save_path, retries=3, delay=2):
        """保存单封邮件"""
        template_file = os.path.join(Config.TEMPLATES_FOLDER, "GetItem.xml")
        soap_body = load_template(template_file, Id=email_id, ChangeKey=change_key)

        for attempt in range(retries):
            try:
                response = self.send_soap_request(soap_body)
                if response.status_code == 200:
                    mime_content_element = ET.fromstring(response.content).find(".//t:MimeContent",
                                                                                Config.EXCHANGE_NAMESPACE)
                    email_subject_element = ET.fromstring(response.content).find(".//t:Subject",
                                                                                 Config.EXCHANGE_NAMESPACE)

                    if mime_content_element is None or email_subject_element is None:
                        logging.error("无法找到邮件内容或主题")
                        continue

                    mime_content = mime_content_element.text
                    email_subject = email_subject_element.text

                    if email_subject is None:
                        email_subject = "No_Subject"
                    # 截断文件名，避免过长导致保存失败
                    email_subject = email_subject[:50]
                    email_file = os.path.join(save_path, escape_filename("[Subject]" + email_subject + "-" + email_id[-16:] + ".eml"))
                    save_email_to_file(email_file, mime_content)
                    return
                else:
                    logging.error(f"请求失败: {response.status_code} - {response.text}")
            except requests.RequestException as e:
                logging.error(f"请求发生异常: {e}")

            logging.warning(f"下载邮件失败，重试 {attempt + 1}/{retries} 次")
            time.sleep(delay)

        logging.error(f"邮件下载失败，已达到最大重试次数 ({retries})，邮件ID: {email_id}，邮件key: {change_key}")

    def search_emails(self, folder, search_type, keyword, start, end, save_path):
        """按关键字搜索邮件"""
        template_file = os.path.join(Config.TEMPLATES_FOLDER, "SearchMail.xml")
        max_count = 50  # 每页最大邮件数
        offset = 0  # 起始偏移量
        if search_type == "keyword" and keyword:
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
        elif search_type == "DateTimeReceived" and start and end:
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
        elif search_type == "DateTimeSent" and start and end:
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
        # 循环分页获取邮件

        while True:
            soap_body = load_template(
                template_file, folder_id=folder['id'], folder_key=folder['key'],
                search_condition=search_condition, max_count=max_count, offset=offset
            )
            response = self.send_soap_request(soap_body)

            # 解析返回结果
            root = ET.fromstring(response.content)
            items = root.findall(".//t:ItemId", Config.EXCHANGE_NAMESPACE)

            # 如果没有更多结果，结束循环
            if not items:
                # logging.info("搜索完成，没有更多结果。")
                break

            logging.info(f"文件夹 {folder['name']} 获取到 {len(items)} 封邮件，正在下载...")

            # 下载当前页的邮件
            for item in items:
                self.save_single_email(item.get('Id'), item.get('ChangeKey'), save_path)
            # 当前页邮件数量小于最大数量时，表示已经是最后一页
            if len(items) < max_count:
                offset += len(items)
                break

            # 更新偏移量，处理下一页
            offset += max_count
            logging.info(f"已处理 {offset} 封邮件，继续下一页...")
        # 当该文件夹下邮件为空
        if offset == 0:
            logging.info(f"文件夹 {folder['name']} 中没有符合条件的邮件。")
            return 0
        logging.info(f"文件夹 {folder['name']} 符合条件的邮件下载完成, 共下载 {offset} 封邮件, 所有文件保存路径：{save_path}")
        return offset

    def handle_people(self):
        logging.info("查找所有人员")
        all_people = []
        all_email = []

        for char in "abcdefghijklmnopqrstuvwxyz":
            logging.info(f"正在查找: {char}")
            peoples, emails = self.find_all_people(char)
            all_people.extend(peoples)
            all_email.extend(emails)

        unique_people = sorted(set(all_people))
        unique_emails = sorted(set(all_email))

        with open("peoples.txt", "w", encoding="utf-8") as file:
            for person in unique_people:
                file.write(f"{person}\n")
        logging.info(f"共计 {len(unique_people)} 条人员信息已保存到 'peoples.txt'")

        with open("emails.txt", "w", encoding="utf-8") as file:
            for email in unique_emails:
                file.write(f"{email}\n")
        logging.info(f"共计 {len(unique_emails)} 条邮箱已保存到 'emails.txt'")


    def handle_download(self, args):
        if args.id and args.key:
            save_path = os.path.join(os.getcwd(), args.username, "SingleEmail")
            self.save_single_email(args.id, args.key, save_path)
            return
        if args.folder == "all":
            folders = self.get_folders()
            if not args.y:
                confirm = input(
                    "您即将下载所有文件夹中的邮件。按回车键继续或输入 'Y' 以确认，或输入指定文件夹名称进行下载: ")
                if confirm.lower() not in ['', 'y']:
                    specific_folder = next((f for f in folders if f['name'].lower() == confirm.lower()), None)
                    if specific_folder and int(specific_folder['total_count']) > 0:
                        save_path = os.path.join(os.getcwd(), args.username, specific_folder['name'])
                        self.download_email(specific_folder, save_path)
                    else:
                        logging.error(f"未找到名为 {confirm} 的文件夹或文件夹中没有邮件")
                    return
            for folder in folders:
                if int(folder['total_count']) > 0:
                    save_path = os.path.join(os.getcwd(), args.username, folder['name'])
                    self.download_email(folder, save_path)
        else:
            folder = next((f for f in self.get_folders() if f['name'] == args.folder), None)
            if folder and int(folder['total_count']) > 0:
                save_path = os.path.join(os.getcwd(), args.username, folder['name'])
                self.download_email(folder, save_path)
            else:
                logging.error(f"未找到名为 {args.folder} 的文件夹或文件夹中没有邮件")

    def handle_search(self, args):
        if args.type == "keyword" and not args.keyword:
            logging.error("必须指定搜索关键词（--keyword）")
            sys.exit(1)

        if (args.type == "DateTimeReceived" or args.type == "DateTimeSent") and not args.start and not args.end:
            logging.error("必须指定起始日期（--start）或截止日期（--end）")
            sys.exit(1)

        save_path = os.path.join(os.getcwd(), args.username)
        if args.type == "keyword":
            save_path = os.path.join(save_path, f"Search-{escape_filename(args.keyword)}")
            logging.info(f"开始搜索标题和正文中包含 {args.keyword} 关键字的邮件")
        elif args.type == "DateTimeReceived" or args.type == "DateTimeSent":
            save_path = os.path.join(save_path,
                                     f"Search-{args.type}-From-{escape_filename(args.start)}-To-{escape_filename(args.end)}")
            logging.info(f"开始搜索日期从 {args.start} 到 {args.end} 的邮件")

        if args.folder == "all":
            folders = self.get_folders()
            total_emails = 0

            if not args.y:
                confirm = input(
                    "您即将搜索所有文件夹中的邮件。按回车键继续或输入 'Y' 以确认，或输入指定文件夹名称进行搜索: ")
                if confirm.lower() not in ['', 'y']:
                    specific_folder = next((f for f in folders if f['name'].lower() == confirm.lower()), None)
                    if specific_folder and int(specific_folder['total_count']) > 0:
                        total_emails += self.search_emails(specific_folder, args.type, args.keyword, args.start, args.end,
                                           os.path.join(save_path, specific_folder['name']))
                    else:
                        logging.error(f"未找到名为 {confirm} 的文件夹 或 文件夹中没有邮件")
                    logging.info(f"总共搜索并下载到 {total_emails} 封邮件")
                    return
            for folder in folders:
                if int(folder['total_count']) > 0:
                    total_emails += self.search_emails(folder, args.type, args.keyword, args.start, args.end,
                                   os.path.join(save_path, folder['name']))
            logging.info(f"总共搜索并下载到 {total_emails} 封邮件")
        else:
            folder = next((f for f in self.get_folders() if f['name'] == args.folder), None)
            if folder and int(folder['total_count']) > 0:
                total_emails = self.search_emails(folder, args.type, args.keyword, args.start, args.end,
                                   os.path.join(save_path, folder['name']))
                logging.info(f"总共搜索并下载到 {total_emails} 封邮件")
            else:
                logging.error(f"未找到名为 {args.folder} 的文件夹 或 文件夹中没有邮件")

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
    except FileNotFoundError:
        logging.error(f"模板文件未找到: {template_file}")
        raise
    except KeyError as e:
        logging.error(f"模板替换时缺少参数: {e}")
        raise
    except IOError as e:
        logging.error(f"无法读取模板文件: {template_file}")
        raise e

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


def parse_arguments():
    parser = argparse.ArgumentParser(description="EWS工具, 用于从Exchange服务器下载邮件，搜索邮件，获取人员信息等。邮件搜索和下载功能是按照日期由近及远的顺序进行的。")
    parser.add_argument("--host", required=True, help="目标Exchange服务器")
    parser.add_argument("-u", "--username", required=True, help="用户名")
    parser.add_argument("-p", "--password", required=False, help="明文密码")
    parser.add_argument("--hash", required=False, help="哈希")
    parser.add_argument("--proxy", help="HTTP代理")
    parser.add_argument("--tls", choices=["1.0", "1.1", "1.2", "1.3"], help="指定TLS版本")

    # 创建子命令解析器
    subparsers = parser.add_subparsers(dest="module", required=True, help="功能模块")

    # 获取人员信息模块
    subparsers.add_parser("people", help="获取所有人员信息及邮箱地址")
    # 获取文件夹列表模块
    subparsers.add_parser("folders", help="获取文件夹列表")
    # 下载模块
    download_parser = subparsers.add_parser("download", help="下载指定邮箱文件夹的邮件")
    download_parser.add_argument("--folder",default="all", help="下载指定邮箱文件夹的邮件")
    download_parser.add_argument("--id", help="要下载的单个邮件的ID")
    download_parser.add_argument("--key", help="要下载的单个邮件的ChangeKey")
    download_parser.add_argument("-y", action="store_true", help="跳过确认")
    # 搜索模块
    search_parser = subparsers.add_parser("search", help="搜索邮件")
    search_parser.add_argument("-t", "--type",choices=["keyword", "DateTimeReceived", "DateTimeSent"], required=True, help="搜索类型")
    search_parser.add_argument("-k", "--keyword", help="搜索关键词")
    search_parser.add_argument("--start", help="搜索邮件时的开始日期 (格式: YYYY-MM-DD)")
    search_parser.add_argument("--end", default=datetime.today().strftime('%Y-%m-%d'), help="搜索邮件时的结束日期 (格式: YYYY-MM-DD)")
    search_parser.add_argument("--folder", default="all",help="要搜索的文件夹,'all'表示所有文件夹")
    search_parser.add_argument("-y", action="store_true", help="跳过确认")
    
    return parser.parse_args()

def get_password_or_hash(args):
    if args.password:
        return args.password
    elif args.hash:
        return f"00000000000000000000000000000000:{args.hash.upper()}"
    else:
        logging.error("请输入密码或哈希")
        sys.exit(1)




def main():
    try:
        args = parse_arguments()
        password_or_hash = get_password_or_hash(args)
        tls_version = 0x0301  # 默认TLS 1.0
        if args.tls == "1.0":
            tls_version = 0x0301
        elif args.tls == "1.1":
            tls_version = 0x0302
        elif args.tls == "1.2":
            tls_version = 0x0303
        elif args.tls == "1.3":
            tls_version = 0x0304
        else:
            logging.warning("未指定TLS版本，将使用默认值TLS 1.0")
        client = EWSClient(args.host, args.username, password_or_hash, args.proxy, tls_version)

        if args.module == "people":
            client.handle_people()
        elif args.module == "folders":
            client.get_folders()
        elif args.module == "download":
            client.handle_download(args)
        elif args.module == "search":
            client.handle_search(args)
        else:
            logging.warning("请输入功能参数!")
    except KeyboardInterrupt:
        logging.info("程序已退出")
        sys.exit(0)

if __name__ == "__main__":
    main()
