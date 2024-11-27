# 项目名称

这是一个用于与Exchange服务器进行交互的工具，支持下载邮件、搜索邮件、获取人员信息等功能。

## 功能

- **下载邮件**：从指定的邮箱文件夹（如收件箱、已发送邮件）下载邮件。
- **搜索邮件**：按关键字或日期范围搜索邮件。
- **获取人员信息**：获取所有人员的姓名和邮箱地址。
- **获取文件夹列表**：获取邮箱中的所有文件夹列表。

## 安装

1. 克隆项目到本地：
    ```bash
    git clone <项目地址>
    ```
2. 进入项目目录：
    ```bash
    cd <项目目录>
    ```
3. 安装依赖：
    ```bash
    pip install -r requirements.txt
    ```

## 使用方法

### 命令行参数

- `--host`：目标Exchange服务器地址（必填）。
- `--username`：用户名（必填）。
- `--password`：明文密码（可选）。
- `--hash`：哈希密码（可选）。
- `--proxy`：HTTP代理（可选）。
- `--people`：获取所有人员信息及邮箱地址（可选）。
- `--folders`：获取文件夹列表（可选）。
- `--download`：下载指定邮箱文件夹的邮件（可选，值为`inbox`、`sentitems`或`all`）。
- `--search`：搜索类型（可选，值为`keyword`、`DateTimeReceived`或`DateTimeSent`）。
- `--keyword`：搜索关键词（可选）。
- `--start`：搜索邮件时的开始日期（格式：YYYY-MM-DD，可选）。
- `--end`：搜索邮件时的结束日期（格式：YYYY-MM-DD，可选）。
- `--email_id`：要下载的单个邮件的ID（可选）。
- `--change_key`：要下载的单个邮件的ChangeKey（可选）。
- `--folder`：要搜索的文件夹（可选，值为`inbox`、`sentitems`或`all`）。

### 示例

1. 获取所有人员信息：
    ```bash
    python ewstools.py --host <host> --username <username> --password <password> --people
    python ewstools.py --host <host> --username <username> --hash <hash> --people
    ```

2. 获取文件夹列表：
    ```bash
    python ewstools.py --host <host> --username <username> --password <password> --folders
    ```

3. 下载邮件（按照日期从近到远进行下载）：
    ```bash
    # 下载收件箱和发件箱中的所有邮件
    python ewstools.py --host <host> --username <username> --password <password> --download all
    # 下载收件箱中的所有邮件
    python ewstools.py --host <host> --username <username> --password <password> --download inbox
    # 下载发件箱中的所有邮件
    python ewstools.py --host <host> --username <username> --password <password> --download sentitems
    ```

4. 按关键字搜索邮件（按照日期从近到远进行下载）：
    ```bash
    # 在收件箱和发件箱中搜索关键字
    python ewstools.py --host <host> --username <username> --password <password> --search keyword --keyword <keyword> --folder all
    # 在收件箱中搜索关键字
    python ewstools.py --host <host> --username <username> --password <password> --search keyword --keyword <keyword> --folder inbox
    # 在发件箱中搜索关键字
    python ewstools.py --host <host> --username <username> --password <password> --search keyword --keyword <keyword> --folder sentitems
    ```

5. 按日期范围搜索邮件并下载（按照日期从近到远进行下载）：
    ```bash
    # 在收件箱和发件箱中搜索日期范围
    python ewstools.py --host <host> --username <username> --password <password> --search DateTimeReceived --start <start_date> --end <end_date> --folder all
    # 在收件箱中搜索日期范围
    python ewstools.py --host <host> --username <username> --password <password> --search DateTimeReceived --start <start_date> --end <end_date> --folder inbox
    # 在发件箱中搜索日期范围
    python ewstools.py --host <host> --username <username> --password <password> --search DateTimeSent --start <start_date> --end <end_date> --folder sentitems
    # 也可以不写--end参数，默认是到当前日期
    python ewstools.py --host <host> --username <username> --password <password> --search DateTimeReceived --start <start_date> --folder inbox
    ```

## 配置

在`ewstools.py`文件中，可以配置以下参数：

- `TEMPLATES_FOLDER`：模板文件夹路径。
- `HTTP_PROTO`：HTTP协议（默认`https`）。
- `EXCHANGE_NAMESPACE`：Exchange命名空间。
- `HEADERS`：HTTP请求头。

## 日志

日志默认输出到控制台，可以在`logging.basicConfig`中配置日志级别和格式。

## 注意事项

- 确保提供的用户名和密码（或哈希）具有访问Exchange服务器的权限。
- 使用代理时，请确保代理服务器配置正确。

## 许可证

此项目遵循MIT许可证。详细信息请参阅`LICENSE`文件。