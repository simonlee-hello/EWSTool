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
    except FileNotFoundError:
        logging.error(f"模板文件未找到: {template_file}")
        raise
    except KeyError as e:
        logging.error(f"模板替换时缺少参数: {e}")
        raise
    except IOError as e:
        logging.error(f"无法读取模板文件: {template_file}")
        raise e

def send_soap_request(session, host, soap_body, retries=3, delay=2):
    """发送SOAP请求并处理401错误"""
    url = f"{HTTP_PROTO}://{host}/ews/exchange.asmx"

    for attempt in range(retries):
        try:
            response = session.post(url, data=soap_body, headers=HEADERS, verify=False)

            if response.status_code == 200 and "NoError" in response.text:
                return response

            if response.status_code == 401:
                logging.warning(f"认证失败，重试 {attempt + 1}/{retries} 次")
                time.sleep(delay)  # 在重试前等待一段时间

            else:
                logging.error(f"请求失败: {response.status_code} - {response.text}")
                break
        except requests.RequestException as e:
            logging.error(f"请求发生异常: {e}")

    logging.error(f"请求失败，已达到最大重试次数 ({retries})")
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
    root = ET.fromstring(response.content)

    # 遍历所有 Persona 元素并提取人员信息
    peoples = set()
    emails = set()
    for persona in root.findall(".//t:Persona", namespaces=EXCHANGE_NAMESPACE):

            display_name = persona.findtext(".//t:DisplayName", namespaces=EXCHANGE_NAMESPACE)
            surname = persona.findtext(".//t:Surname", namespaces=EXCHANGE_NAMESPACE)
            email_address = persona.findtext(".//t:EmailAddress/t:EmailAddress", namespaces=EXCHANGE_NAMESPACE)
            person_info = f"显示名: {display_name}, 姓氏: {surname}, 邮箱: {email_address}"
            peoples.add(person_info)
            emails.add(email_address)

    return peoples, emails

def get_folders(session, host):
    template_file = os.path.join(TEMPLATES_FOLDER, "ListFolder.xml")
    soap_body = load_template(template_file)
    response = send_soap_request(session, host, soap_body)
    root = ET.fromstring(response.content)
    folder_elements = root.findall('.//t:Folder', namespaces=EXCHANGE_NAMESPACE)
    if not folder_elements:
        logging.info("没有找到文件夹。")
        return []

    folders = []
    for folder in folder_elements:
        folder_name = folder.findtext(".//t:DisplayName", namespaces=EXCHANGE_NAMESPACE)
        folder_id = folder.find(".//t:FolderId", namespaces=EXCHANGE_NAMESPACE).get('Id')
        folder_key = folder.find(".//t:FolderId", namespaces=EXCHANGE_NAMESPACE).get('ChangeKey')
        folder_total_count = folder.findtext(".//t:TotalCount", namespaces=EXCHANGE_NAMESPACE)
        folder_child_folder_count = folder.findtext(".//t:ChildFolderCount", namespaces=EXCHANGE_NAMESPACE)
        folder_unread_count = folder.findtext(".//t:UnreadCount", namespaces=EXCHANGE_NAMESPACE)

        folders.append({'name': folder_name, 'id': folder_id, 'key': folder_key, 'total_count': folder_total_count, 'child_folder_count': folder_child_folder_count,'unread_count':folder_unread_count})
        logging.info(f"找到文件夹: {folder_name} 邮件数量共计: {folder_total_count} 子文件夹数量: {folder_child_folder_count} 未读数量: {folder_unread_count}")

    return folders

def get_email_count(session, host, folder):
    """获取指定文件夹中的邮件总数"""
    template_file = os.path.join(TEMPLATES_FOLDER, "GetSizeOfFolder.xml")
    soap_body = load_template(template_file, folder=folder)
    response = send_soap_request(session, host, soap_body)

    # 解析文件夹的邮件总数
    folder_item_count = ET.fromstring(response.content).find(".//t:TotalCount", EXCHANGE_NAMESPACE)
    if folder_item_count is not None:
        return int(folder_item_count.text)
    else:
        logging.warning(f"无法获取文件夹 {folder} 的邮件总数，默认为 0。")
        return 0

def download_email(session, host, folder, save_path):
    # Step 1: 获取文件夹中邮件的总数量
    total_count = get_email_count(session, host, folder)
    if total_count == 0:
        logging.info(f"文件夹 {folder} 中没有邮件，无需下载。")
        return

    logging.info(f"文件夹 {folder} 中有 {total_count} 封邮件，准备下载...")

    # Step 2: 分页下载邮件
    template_file = os.path.join(TEMPLATES_FOLDER, "ListMailOfFolder.xml")
    size = 50  # 每页的邮件数量
    offset = 0  # 初始偏移量

    while offset < total_count:
        # 使用模板生成 SOAP 请求体
        soap_body = load_template(template_file, folder=folder, size=str(size), offset=str(offset))
        response = send_soap_request(session, host, soap_body)

        # 解析返回的邮件列表
        items = ET.fromstring(response.content).findall(".//t:ItemId", EXCHANGE_NAMESPACE)
        if not items:
            break

        # 下载当前批次的邮件
        for item in items:
            email_id = item.get("Id")
            change_key = item.get("ChangeKey")
            save_single_email(session, host, email_id, change_key, save_path)

        if total_count < size:
            break
        # 增加偏移量以获取下一页
        offset += size
        logging.info(f"已下载 {offset} 封邮件，继续下一页...")

    logging.info(f"文件夹 {folder} 内邮件下载完成, 共下载 {total_count} 封邮件, 所有文件保存路径：{save_path}")

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
    max_count = 50  # 每页最大邮件数
    offset = 0  # 起始偏移量
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
    # 循环分页获取邮件

    while True:
        soap_body = load_template(
            template_file, folder_path=folder_path,
            search_condition=search_condition, max_count=max_count, offset=offset
        )
        response = send_soap_request(session, host, soap_body)

        # 解析返回结果
        root = ET.fromstring(response.content)
        items = root.findall(".//t:ItemId", EXCHANGE_NAMESPACE)

        # 如果没有更多结果，结束循环
        if not items:
            logging.info("搜索完成，没有更多结果。")
            break

        logging.info(f"获取到 {len(items)} 封邮件，正在下载...")

        # 下载当前页的邮件
        for item in items:
            save_single_email(session, host, item.get('Id'), item.get('ChangeKey'), save_path)

        if len(items) < max_count:
            offset += len(items)
            break

        # 更新偏移量，处理下一页
        offset += max_count
        logging.info(f"已处理 {offset} 封邮件，继续下一页...")
    logging.info(f"符合条件的邮件下载完成, 共下载 {offset} 封邮件, 所有文件保存路径：{save_path}")

def main():
    parser = argparse.ArgumentParser(description="EWS工具")
    parser.add_argument("--host", required=True, help="目标Exchange服务器")
    parser.add_argument("--username", required=True, help="用户名")
    parser.add_argument("--password", required=False, help="明文密码")
    parser.add_argument("--hash", required=False, help="哈希")
    parser.add_argument("--proxy", help="HTTP代理")

    # command to get all people`s email
    parser.add_argument("--people", action="store_true", required=False, help="获取所有人员信息及邮箱地址")
    # command to list the folder
    parser.add_argument("--folders", required=False, action="store_true",help="获取文件夹列表")
    # command to download emails
    parser.add_argument("--download", required=False, choices=["inbox", "sentitems", "all"], help="下载指定邮箱文件夹的邮件")
    # New arguments for search command
    parser.add_argument("--search", choices=["keyword", "DateTimeReceived", "DateTimeSent"], help="搜索类型")
    parser.add_argument("--keyword", help="搜索关键词")
    parser.add_argument("--start", help="搜索邮件时的开始日期 (格式: YYYY-MM-DD)")
    parser.add_argument("--end",default=datetime.today().strftime('%Y-%m-%d'), help="搜索邮件时的结束日期 (格式: YYYY-MM-DD)")

    args = parser.parse_args()

    if args.password:
        password_or_hash = args.password
    elif args.hash:
        password_or_hash = f"00000000000000000000000000000000:{args.hash.upper()}"
    else:
        logging.error("请输入密码或哈希")
        sys.exit(1)
    proxies = {"http": args.proxy, "https": args.proxy} if args.proxy else {}

    session = requests.Session()
    session.auth = HttpNtlmAuth(args.username, password_or_hash)
    session.proxies = proxies

    if args.people:
        logging.info("查找所有人员")
        all_people = []
        all_email = []

        # 遍历 a-z 进行查找
        for char in "abcdefghijklmnopqrstuvwxyz":
            logging.info(f"正在查找: {char}")
            peoples, emails = find_all_people(session, args.host, char)
            all_people.extend(peoples)  # 使用 extend 来合并结果
            all_email.extend(emails)  # 使用 extend 来合并结果

        # 去重
        unique_people = sorted(set(all_people))  # 去重并排序
        unique_emails = sorted(set(all_email))  # 去重并排序

        # 将结果写入文件
            # 保存人员信息
        with open("peoples.txt", "w", encoding="utf-8") as file:
            for person in unique_people:
                file.write(f"{person}\n")
        logging.info(f"共计 {len(unique_people)} 条唯一人员信息已保存到 'peoples.txt'")

        # 保存邮箱信息
        with open("emails.txt", "w", encoding="utf-8") as file:
            for email in unique_emails:
                file.write(f"{email}\n")
        logging.info(f"共计 {len(unique_emails)} 条唯一邮箱已保存到 'emails.txt'")
    elif args.folders:
        get_folders(session, args.host)
    elif args.download:
        if args.download == "all":
            for folder in ['inbox', 'sentitems']:
                save_path = os.path.join(os.getcwd(), args.username, folder)
                download_email(session, args.host, folder, save_path)
        else:
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
    else:
        logging.warning("请输入功能参数!")
if __name__ == "__main__":
    main()
