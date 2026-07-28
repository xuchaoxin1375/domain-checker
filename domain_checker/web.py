"""Flask Web 层：HTTP API 与服务启动。

仅做参数解析/响应组装，业务逻辑在其它模块。模块级 ``app`` 供
``python app.py`` 与 ``flask --app domain_checker.web run`` 使用，
也可用 ``create_app()`` 工厂自建实例。
"""

import json
import logging
import os
import threading
from datetime import datetime

from flask import Flask, jsonify, render_template, request, send_file

from . import settings
from .db import clear_old_history, delete_history, get_history, get_history_detail, init_db
from .domains import parse_domain_input
from .export import create_export_file
from .settings import (
    CONFIG,
    PLATFORMS,
    TIMEOUT_MAX_SECONDS,
    TIMEOUT_MIN_SECONDS,
    config_lock,
    get_server_info,
    load_config_from_file,
    normalize_query_mode,
    normalize_timeout,
    public_config,
    save_config_to_file,
)
from .state import task_lock, task_pause_flags, task_storage
from .tasks import generate_task_id, process_domains_async, retry_domains_async

# 日志配置（CLI 场景下各自配置，这里仅对未配置过的环境生效）
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


def _restore_task_from_history(task_id: str) -> bool:
    """将已落库任务恢复到内存，使服务重启后的历史结果仍可重查。"""
    with task_lock:
        if task_id in task_storage:
            return True

    history, stored_results = get_history_detail(task_id)
    if not history or not stored_results:
        return False

    try:
        saved_config = json.loads(history.get('config') or '{}')
    except (TypeError, json.JSONDecodeError):
        saved_config = {}

    results = []
    for stored in stored_results:
        result = dict(stored)
        result['resolved'] = (
            True if result.get('resolved') == 1
            else False if result.get('resolved') == 0
            else None
        )
        records = result.get('dns_records')
        result['dns_records'] = records.split(',') if records else []
        results.append(result)

    completed_at = history.get('completed_at') or datetime.now().isoformat()
    restored_task = {
        'status': 'completed',
        'total': history.get('domain_count') or len(results),
        'completed': len(results),
        'results': results,
        'logs': [{
            'time': datetime.now().strftime('%H:%M:%S'),
            'level': 'info',
            'message': '已从历史记录恢复任务，可继续重新查询',
        }],
        'refresh': True,
        'created_at': history.get('created_at') or completed_at,
        'completed_at': completed_at,
        'platform': saved_config.get('platform', 'whois'),
        'operation': 'completed',
        'query_mode': 'unlimited',
        'query_timeout': normalize_timeout(saved_config.get('timeout', CONFIG['timeout'])),
        'operation_total': 0,
        'operation_completed': 0,
        'operation_started_at': completed_at,
        'duration_seconds': None,
    }
    with task_lock:
        task_storage.setdefault(task_id, restored_task)
    return True


def create_app() -> Flask:
    """应用工厂：初始化数据库、加载持久化配置、注册路由。"""
    flask_app = Flask(__name__, template_folder=str(settings.TEMPLATES_DIR))
    flask_app.secret_key = os.urandom(24)

    # 初始化数据库并加载持久化配置（init_db 幂等，兼容 debug reloader 双进程）
    init_db()
    load_config_from_file()

    _register_routes(flask_app)
    return flask_app


def _register_routes(flask_app: Flask):

    @flask_app.route('/')
    def index():
        return render_template('index.html', config=CONFIG)

    @flask_app.route('/api/config', methods=['GET', 'POST'])
    def api_config():
        if request.method == 'POST':
            data = request.get_json()
            with config_lock:
                old_allow_lan = bool(CONFIG.get('allow_lan_access', True))
                for key in CONFIG:
                    if key in data:
                        val = data[key]
                        if key in ['max_domains_per_batch', 'max_retries', 'timeout', 'max_workers']:
                            val = int(val)
                            if key == 'timeout':
                                val = max(TIMEOUT_MIN_SECONDS, min(TIMEOUT_MAX_SECONDS, val))
                        elif key in ['rate_limit_delay', 'retry_delay']:
                            val = float(val)
                        elif key in ['proxy_enabled', 'allow_lan_access']:
                            val = bool(val)
                        elif key == 'platform':
                            val = val if val in PLATFORMS else 'whois'
                        CONFIG[key] = val
                lan_changed = bool(data.get('allow_lan_access', old_allow_lan)) != old_allow_lan

            # 持久化到文件，保证重启后配置（含局域网访问设置）仍可生效
            save_config_to_file()
            logger.info(
                f"配置已更新: platform={CONFIG['platform']}, "
                f"proxy_enabled={CONFIG['proxy_enabled']}, "
                f"allow_lan_access={CONFIG['allow_lan_access']}")

            message = '配置已更新'
            if lan_changed:
                message += '；局域网访问设置将在重启服务后生效'
            return jsonify({
                'message': message,
                'config': public_config(),
                'platforms': PLATFORMS,
                'server': get_server_info()
            })
        return jsonify({
            **public_config(),
            'platforms': PLATFORMS,
            'server': get_server_info()
        })

    @flask_app.route('/api/query', methods=['POST'])
    def api_query():
        data = request.get_json()
        input_text = data.get('domains', '')
        platform = data.get('platform', 'whois')
        query_mode = normalize_query_mode(data.get('query_mode', data.get('mode', 'unlimited')))
        with config_lock:
            query_timeout = normalize_timeout(data.get('query_timeout', CONFIG['timeout']))

        # 解析输入（支持URL格式）
        domains = parse_domain_input(input_text)

        if not domains:
            return jsonify({'error': '请输入有效域名'}), 400

        with config_lock:
            max_batch = CONFIG['max_domains_per_batch']
            # 如果传入了平台参数，先更新配置
            if platform in PLATFORMS:
                CONFIG['platform'] = platform

        if len(domains) > max_batch:
            return jsonify({'error': f'单次查询最多{max_batch}个域名'}), 400

        task_id = generate_task_id()

        with task_lock:
            task_storage[task_id] = {
                'status': 'processing', 'total': len(domains), 'completed': 0,
                'results': [], 'logs': [], 'refresh': False,
                'created_at': datetime.now().isoformat(), 'completed_at': None,
                'platform': platform, 'operation': 'query',
                'query_mode': query_mode,
                'query_timeout': query_timeout,
                'operation_total': len(domains), 'operation_completed': 0,
                'operation_started_at': datetime.now().isoformat(),
                'duration_seconds': None,
            }

        logger.info(f"[任务{task_id}] 创建，使用平台: {PLATFORMS.get(platform, {}).get('name', platform)}")

        thread = threading.Thread(target=process_domains_async, args=(domains, task_id))
        thread.daemon = True
        thread.start()

        return jsonify({
            'task_id': task_id, 'total': len(domains), 'platform': platform,
            'query_mode': query_mode,
            'query_timeout': query_timeout,
            'message': f'任务已创建，使用{PLATFORMS.get(platform, {}).get("name", platform)}处理 {len(domains)} 个域名'
        })

    @flask_app.route('/api/status/<task_id>')
    def api_status(task_id):
        with task_lock:
            task = task_storage.get(task_id)
            task = dict(task) if task else None
        if not task:
            return jsonify({'error': '任务不存在'}), 404

        operation = task.get('operation', 'query')
        operation_total = task.get('operation_total', task['total'])
        operation_completed = task.get('operation_completed', task['completed'])
        started_at = datetime.fromisoformat(task.get('operation_started_at', task['created_at']))
        ended_at = datetime.fromisoformat(task['completed_at']) if task.get('completed_at') else datetime.now()
        elapsed_seconds = max(0, round((ended_at - started_at).total_seconds(), 2))
        return jsonify({
            'status': task['status'], 'total': task['total'],
            'completed': task['completed'],
            'progress': round(operation_completed / operation_total * 100, 1) if operation_total > 0 else 0,
            'paused': task_pause_flags.get(task_id, False),
            'query_mode': task.get('query_mode', 'unlimited'),
            'query_timeout': task.get('query_timeout'),
            'operation': operation,
            'operation_total': operation_total,
            'operation_completed': operation_completed,
            'elapsed_seconds': elapsed_seconds,
            'duration_seconds': task.get('duration_seconds'),
        })

    @flask_app.route('/api/results/<task_id>')
    def api_results(task_id):
        with task_lock:
            task = task_storage.get(task_id)
            if task:
                results = list(task['results'])
                logs = list(task['logs'])
                refresh = task.get('refresh', False)
                status = task['status']
                total = task['total']
                completed = task['completed']
                if refresh:
                    task['refresh'] = False

        if not task:
            return jsonify({'error': '任务不存在'}), 404

        log_level = request.args.get('log_level', 'all')
        log_after = max(0, request.args.get('log_after', 0, type=int))
        new_logs = logs[log_after:]
        if log_level != 'all':
            new_logs = [log for log in new_logs if log.get('level') == log_level]

        return jsonify({
            'status': status, 'results': results,
            'total': total, 'completed': completed,
            'logs': new_logs, 'log_cursor': len(logs), 'refresh': refresh,
            'paused': task_pause_flags.get(task_id, False),
        })

    @flask_app.route('/api/pause/<task_id>', methods=['POST'])
    def api_pause(task_id):
        task_pause_flags[task_id] = True
        with task_lock:
            if task_id in task_storage:
                task_storage[task_id]['logs'].append({
                    'time': datetime.now().strftime('%H:%M:%S'),
                    'level': 'warn',
                    'message': '⏸ 任务已暂停'
                })
        return jsonify({'message': '已暂停', 'paused': True})

    @flask_app.route('/api/resume/<task_id>', methods=['POST'])
    def api_resume(task_id):
        task_pause_flags[task_id] = False
        with task_lock:
            if task_id in task_storage:
                task_storage[task_id]['logs'].append({
                    'time': datetime.now().strftime('%H:%M:%S'),
                    'level': 'info',
                    'message': '▶ 任务已继续'
                })
        return jsonify({'message': '已继续', 'paused': False})

    @flask_app.route('/api/retry/<task_id>', methods=['POST'])
    def api_retry(task_id):
        data = request.get_json(silent=True) or {}
        raw_domains = data.get('domains', [])
        if not isinstance(raw_domains, list):
            return jsonify({'error': 'domains 必须是域名数组'}), 400
        domains = parse_domain_input('\n'.join(str(domain) for domain in raw_domains))
        if not domains:
            return jsonify({'error': '请选择域名'}), 400
        with config_lock:
            default_timeout = CONFIG['timeout']

        if not _restore_task_from_history(task_id):
            return jsonify({'error': '任务不存在或尚无可重查结果'}), 404

        with task_lock:
            task = task_storage.get(task_id)
            if task['status'] == 'processing':
                return jsonify({'error': '任务正在查询，请等待当前操作结束'}), 409
            available_domains = {result['domain'] for result in task['results']}
            unknown_domains = [domain for domain in domains if domain not in available_domains]
            if unknown_domains:
                return jsonify({'error': f'域名不属于当前任务: {", ".join(unknown_domains)}'}), 400
            query_mode = normalize_query_mode(data.get('query_mode', data.get('mode', task.get('query_mode', 'unlimited'))))
            query_timeout = normalize_timeout(data.get('query_timeout', task.get('query_timeout', default_timeout)))
            task['refresh'] = True
            task['status'] = 'processing'
            task['operation'] = 'retry'
            task['query_mode'] = query_mode
            task['query_timeout'] = query_timeout
            task['operation_total'] = len(domains)
            task['operation_completed'] = 0
            task['operation_started_at'] = datetime.now().isoformat()
            task['completed_at'] = None
            task['duration_seconds'] = None

        thread = threading.Thread(target=retry_domains_async, args=(domains, task_id))
        thread.daemon = True
        thread.start()

        return jsonify({'message': f'正在重新查询 {len(domains)} 个域名', 'started': True})

    @flask_app.route('/api/retry-failed/<task_id>', methods=['POST'])
    def api_retry_failed(task_id):
        data = request.get_json(silent=True) or {}
        with config_lock:
            default_timeout = CONFIG['timeout']
        if not _restore_task_from_history(task_id):
            return jsonify({'error': '任务不存在或尚无可重查结果'}), 404
        with task_lock:
            task = task_storage.get(task_id)
            if task['status'] == 'processing':
                return jsonify({'error': '任务正在查询，请等待当前操作结束'}), 409
            query_mode = normalize_query_mode(data.get('query_mode', data.get('mode', task.get('query_mode', 'unlimited'))))
            query_timeout = normalize_timeout(data.get('query_timeout', task.get('query_timeout', default_timeout)))
            failed_domains = [r['domain'] for r in task['results']
                              if r['status'] in {'failed', 'timeout', 'invalid'}]
            if not failed_domains:
                return jsonify({'message': '没有需要重试的域名', 'started': False})
            task['refresh'] = True
            task['status'] = 'processing'
            task['operation'] = 'retry'
            task['query_mode'] = query_mode
            task['query_timeout'] = query_timeout
            task['operation_total'] = len(failed_domains)
            task['operation_completed'] = 0
            task['operation_started_at'] = datetime.now().isoformat()
            task['completed_at'] = None
            task['duration_seconds'] = None

        thread = threading.Thread(target=retry_domains_async, args=(failed_domains, task_id))
        thread.daemon = True
        thread.start()

        return jsonify({'message': f'正在重试 {len(failed_domains)} 个域名', 'started': True})

    @flask_app.route('/api/export/<task_id>')
    def api_export(task_id):
        format_type = request.args.get('format', 'csv')
        filter_type = request.args.get('filter', 'all')
        selected = request.args.get('selected', '')

        # 优先从内存获取，否则从数据库
        with task_lock:
            task = task_storage.get(task_id)

        if task:
            results = task['results']
        else:
            # 从数据库获取（服务重启后仍可导出历史结果）
            _, results = get_history_detail(task_id)
            results = [{'domain': r['domain'], 'status': r['status'],
                        'whois_status': r['whois_status'], 'hold_status': r['hold_status'],
                        'registrar': r['registrar'],
                        'registration_date': r['registration_date'], 'expiration_date': r['expiration_date'],
                        'updated_date': r['updated_date'], 'name_servers': r['name_servers'],
                        'dnssec': r['dnssec'], 'resolved': bool(r['resolved']) if r['resolved'] is not None else None,
                        'block_reason': r['block_reason'],
                        'dns_records': r['dns_records'].split(',') if r['dns_records'] else [],
                        'error': r['error'], 'query_time': r['query_time'],
                        'query_duration_seconds': r['query_duration_seconds']} for r in results]

        if not results:
            return jsonify({'error': '没有可导出的数据'}), 400

        if selected:
            selected_domains = selected.split(',')
            results = [r for r in results if r['domain'] in selected_domains]

        if not results:
            return jsonify({'error': '没有可导出的数据'}), 400

        try:
            filepath, filename = create_export_file(results, format_type, filter_type)
            mimetype = 'text/csv' if format_type == 'csv' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            return send_file(filepath, mimetype=mimetype, as_attachment=True, download_name=filename)
        except Exception as e:
            logger.error(f"导出失败: {e}")
            return jsonify({'error': str(e)}), 500

    @flask_app.route('/api/cancel/<task_id>', methods=['POST'])
    def api_cancel(task_id):
        task_pause_flags[task_id] = True
        with task_lock:
            if task_id in task_storage:
                task_storage[task_id]['status'] = 'cancelled'
                task_storage[task_id]['logs'].append({
                    'time': datetime.now().strftime('%H:%M:%S'),
                    'level': 'warn',
                    'message': '任务已取消'
                })
        return jsonify({'message': '任务已取消'})

    # ============== 历史记录API ==============

    @flask_app.route('/api/history')
    def api_history():
        """获取历史记录列表。"""
        limit = request.args.get('limit', 50, type=int)
        history = get_history(limit)
        return jsonify({'history': history})

    @flask_app.route('/api/history/<task_id>')
    def api_history_detail(task_id):
        """获取历史详情。"""
        history, results = get_history_detail(task_id)
        if not history:
            return jsonify({'error': '记录不存在'}), 404
        return jsonify({'history': history, 'results': results})

    @flask_app.route('/api/history/<task_id>', methods=['DELETE'])
    def api_delete_history(task_id):
        """删除历史记录。"""
        delete_history(task_id)
        return jsonify({'message': '已删除'})

    @flask_app.route('/api/history/clear', methods=['POST'])
    def api_clear_history():
        """清理旧历史。"""
        data = request.get_json() or {}
        days = data.get('days', 30)
        deleted = clear_old_history(days)
        return jsonify({'message': f'已清理 {deleted} 条记录'})

    return flask_app


def get_lan_ip():
    """获取本机局域网IP（用于提示访问地址）。"""
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))  # 不会真正发包，仅用于探测出口网卡
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def run_server():
    """读取配置，选择监听地址并启动开发服务器。"""
    from . import __version__

    host = os.environ.get('DOMAIN_CHECKER_HOST')
    if not host:
        with config_lock:
            allow_lan = bool(CONFIG.get('allow_lan_access', True))
        host = '0.0.0.0' if allow_lan else '127.0.0.1'
    else:
        allow_lan = host == '0.0.0.0'

    port = int(os.environ.get('DOMAIN_CHECKER_PORT', '5000'))
    debug = os.environ.get('DOMAIN_CHECKER_DEBUG', '1') != '0'
    settings.SERVER_RUNTIME.update({'host': host, 'port': port})

    v = __version__.rpartition('.')[0]
    print("=" * 50)
    print(f"域名批量查询系统 v{v}")
    print("=" * 50)
    print(f"本机访问: http://localhost:{port}")
    if allow_lan:
        lan_ip = get_lan_ip()
        print("局域网访问: 已开启" + (f"（局域网设备可通过 http://{lan_ip}:{port} 访问）" if lan_ip else ""))
    else:
        print("局域网访问: 已关闭（仅本机可访问）")
    print(f"数据目录: {settings.DATA_DIR}")
    print(f"数据库: {settings.DB_PATH}")
    print("=" * 50)
    app.run(debug=debug, host=host, port=port, threaded=True)


# 模块级应用实例（init_db / 加载配置随导入完成）
app = create_app()
