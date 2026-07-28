#!/usr/bin/env python3
"""
命令行域名批量查询工具
用法: python cli.py domains.txt
      python cli.py --single example.com
      cat domains.txt | python cli.py
"""

import logging
import os
import sys
import time

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from domain_checker.checker import process_single_domain as process_domain
from domain_checker.export import create_csv
from domain_checker.settings import CONFIG


def read_domains_from_file(filepath):
    """从文件读取域名"""
    with open(filepath, 'r') as f:
        return [line.strip() for line in f if line.strip()]

def read_domains_from_stdin():
    """从标准输入读取域名"""
    return [line.strip() for line in sys.stdin if line.strip()]

def main():
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.WARNING,
                            format='%(asctime)s - %(levelname)s - %(message)s',
                            datefmt='%H:%M:%S')

    domains = []

    # 解析参数
    if len(sys.argv) > 1:
        if sys.argv[1] == '--single':
            # 单个域名查询
            if len(sys.argv) > 2:
                domains = [sys.argv[2]]
            else:
                print("错误: 请提供域名")
                print("用法: python cli.py --single example.com")
                sys.exit(1)
        elif sys.argv[1] == '--help':
            print(__doc__)
            sys.exit(0)
        else:
            # 从文件读取
            filepath = sys.argv[1]
            if not os.path.exists(filepath):
                print(f"错误: 文件不存在 - {filepath}")
                sys.exit(1)
            domains = read_domains_from_file(filepath)
    else:
        # 尝试从管道读取
        if not sys.stdin.isatty():
            domains = read_domains_from_stdin()
        else:
            print(__doc__)
            sys.exit(1)

    if not domains:
        print("错误: 未提供域名")
        sys.exit(1)

    # 去重
    domains = list(set(domains))

    print(f"{'='*60}")
    print("域名批量查询工具")
    print(f"{'='*60}")
    print(f"待查询域名数: {len(domains)}")
    print(f"限流间隔: {CONFIG['rate_limit_delay']}秒")
    print(f"最大重试: {CONFIG['max_retries']}次")
    print(f"并发线程: {CONFIG['max_workers']}")
    print(f"{'='*60}")
    print()

    # 处理域名
    results = []
    start_time = time.time()

    for i, domain in enumerate(domains, 1):
        print(f"[{i}/{len(domains)}] 查询: {domain}...", end=" ", flush=True)

        result = process_domain(domain)
        results.append(result)

        if result['status'] == 'success':
            exp = result['expiration_date'] or '未知'
            print(f"✓ 成功 | 过期: {exp}")
        else:
            print(f"✗ 失败 | {result['error']}")

    elapsed = time.time() - start_time

    # 统计
    print()
    print(f"{'='*60}")
    print("查询完成!")
    print(f"{'='*60}")

    success = sum(1 for r in results if r['status'] == 'success')
    failed = len(results) - success
    blocked = sum(1 for r in results if r['resolved'] is False)

    print(f"总计: {len(results)} | 成功: {success} | 失败: {failed} | 未解析: {blocked}")
    print(f"耗时: {elapsed:.1f}秒")
    print(f"平均: {elapsed/len(results):.2f}秒/域名")
    print()

    # 导出CSV
    filepath = create_csv(results, int(time.time()))
    print(f"✓ 已导出CSV: {filepath}")
    print()

    # 显示失败详情
    failed_results = [r for r in results if r['status'] != 'success']
    if failed_results:
        print("失败域名详情:")
        print("-" * 60)
        for r in failed_results:
            print(f"  {r['domain']}: {r['error']}")

if __name__ == '__main__':
    main()
