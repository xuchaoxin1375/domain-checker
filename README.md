# 域名批量查询系统 v2.3

功能完整的域名批量查询工具，支持多平台查询、主题切换、表格排序。

## 功能特性

✅ **多平台查询** - WHOIS标准/WHOIS XML/RDAP三种查询方式  
✅ **多主题支持** - 浅色/深色/绿色/紫色主题，带涟漪动画效果  
✅ **表格排序** - 点击表头可按任意列排序  
✅ **代理支持** - 支持配置HTTP代理  
✅ **URL解析** - 支持直接粘贴URL，自动提取域名  
✅ **历史记录** - SQLite数据库存储，可查看/导出/删除历史  
✅ **暂停/继续** - 支持随时暂停和继续查询任务  
✅ **实时结果** - 查询过程中实时显示已完成的域名  
✅ **日志级别** - 可按信息/警告/错误过滤日志  
✅ **单个重查** - 点击域名即可重新查询  
✅ **批量重查** - 一键重试所有失败的域名  
✅ **复选框操作** - 选择特定域名进行批量重查/导出  
✅ **灵活导出** - 支持CSV/Excel，可导出全部或选中域名  
✅ **限流控制** - 避免被WHOIS服务器封禁  
✅ **自动重试** - 查询失败自动重试  

## 快速启动

```bash
cd /home/user/domain-checker
pip install -r requirements.txt
python app.py
```

访问 http://localhost:5000

## 新增功能说明

### 多主题切换

支持4种主题：
- 🌓 **浅色** - 默认主题，清新明亮
- 🌙 **深色** - 夜间模式，护眼舒适
- 🌿 **绿色** - 清新绿色，环保风格
- 💜 **紫色** - 优雅紫色，商务风格

点击右上角主题按钮切换，带有流畅的过渡动画。

### 表格排序

点击任意表头即可排序：
- 再次点击反向排序
- 当前排序列会高亮显示
- 支持按域名、状态、注册日期、过期日期等排序

### 多平台查询

| 平台 | 说明 |
|------|------|
| 🔍 WHOIS标准 | 使用标准WHOIS协议查询 |
| 🌐 WHOIS XML | 使用WHOIS XML API查询 |
| 🛡️ RDAP安全 | 使用RDAP协议，更安全可靠 |

### URL解析支持

支持直接粘贴以下格式：

```
example.com
www.example.com
https://example.com
https://www.example.com/path
http://example.com/some/path?query=1
https://www.qq.com/path/to/page
```

### 历史记录

在「历史记录」Tab中可以：
- 查看所有历史查询
- 查看历史详情（重新加载结果到表格）
- 导出历史结果
- 删除单条记录
- 清理30天前的记录

## 配置文件

### 网页端配置

在页面上方配置面板修改：
- 限流间隔（秒）
- 最大重试次数
- 并发线程数
- 超时时间
- 单批上限
- 查询平台
- 代理开关
- 代理地址

### 代码配置

在 `app.py` 中修改 `CONFIG` 字典：

```python
CONFIG = {
    'max_domains_per_batch': 500,     # 单批最大域名数
    'rate_limit_delay': 1.0,          # 请求间隔(秒)
    'max_retries': 3,                # 最大重试次数
    'retry_delay': 2,                 # 重试间隔(秒)
    'timeout': 15,                    # 超时时间(秒)
    'max_workers': 5,                 # 并发线程数
    'platform': 'whois',              # 查询平台
    'proxy_enabled': False,            # 是否启用代理
    'proxy_url': 'http://127.0.0.1:7897',  # 代理地址
}
```

## API 接口

### 配置
```bash
GET  /api/config           # 获取当前配置
POST /api/config           # 修改配置
```

### 查询
```bash
POST /api/query           # 提交查询 {"domains": "a.com\nb.com", "platform": "whois"}
GET  /api/status/<id>    # 获取进度
GET  /api/results/<id>    # 获取结果和日志
```

### 暂停/继续
```bash
POST /api/pause/<id>      # 暂停任务
POST /api/resume/<id>     # 继续任务
```

### 重试
```bash
POST /api/retry/<id>      # 重试指定域名 {"domains": ["a.com"]}
POST /api/retry-failed/<id> # 重试所有失败域名
```

### 历史记录
```bash
GET  /api/history              # 获取历史列表
GET  /api/history/<task_id>   # 获取历史详情
DELETE /api/history/<task_id>  # 删除历史
POST /api/history/clear        # 清理旧历史 {"days": 30}
```

### 导出
```bash
GET /api/export/<id>?format=csv&filter=all&selected=
```

## 数据存储

- 数据库：`data/domain_checker.db`
- 主题偏好：localStorage 自动保存

## .gitignore

已配置忽略：
- Python缓存文件
- 虚拟环境
- IDE配置
- 日志文件
- 临时文件
- 数据库文件

## 命令行工具

```bash
# 从文件读取
python cli.py domains.txt

# 管道输入
cat domains.txt | python cli.py

# 单个查询
python cli.py --single example.com
```

## 注意事项

⚠️ 大量查询时建议启用代理并调高限流间隔  
⚠️ 并发数不宜过大，建议5个以内  
⚠️ 部分域名使用隐私保护，信息可能不完整  
⚠️ 历史记录数据库会随使用逐渐增大，建议定期清理
