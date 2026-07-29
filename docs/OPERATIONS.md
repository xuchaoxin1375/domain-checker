# 服务运行、排查与停止

本文说明如何确认域名查询服务是否运行、定位占用端口的进程，以及在 Windows、macOS、Linux
上安全停止服务。默认端口为 `5000`；如果设置了 `DOMAIN_CHECKER_PORT`，请把命令中的端口替换为实际值。

## 优先使用的停止方式

1. 服务在前台终端运行时，回到该终端按 `Ctrl+C`。
2. 启动终端已经丢失时，先确认端口对应的 PID 和进程名称，再使用系统命令停止。

关闭浏览器页面不会关闭 Python 服务。

## 服务操作日志

“操作日志”独立页面展示最近的服务启动与终止操作，记录持久化在 `data/operations.log`。调试重载模式只记录
实际监听进程，避免同一次启动出现父子进程两条记录。通过 `Ctrl+C` 正常退出会记录“终止”。
操作系统强制结束、断电或进程崩溃时无法保证写入最后一条终止记录。

## Windows

### 查询监听端口

```powershell
Get-NetTCPConnection -LocalPort 5000 -State Listen
```

### 确认进程

```powershell
Get-Process -Id <PID>
```

### 停止服务

确认 PID 属于 Python 域名查询服务后运行：

```powershell
Stop-Process -Id <PID>
```

### 旧版 Windows

查询端口：

```bat
netstat -ano | findstr :5000
```

确认进程：

```bat
tasklist /FI "PID eq <PID>"
```

停止服务：

```bat
taskkill /PID <PID>
```

## macOS

### 查询监听端口

```bash
lsof -nP -iTCP:5000 -sTCP:LISTEN
```

### 确认进程

```bash
ps -p <PID> -o pid,command
```

### 正常停止

```bash
kill <PID>
```

### 强制停止

等待数秒后再次运行 `lsof`。进程确认无响应时，才使用：

```bash
kill -9 <PID>
```

## Linux

### 查询监听端口

```bash
ss -ltnp 'sport = :5000'
```

### 确认进程

```bash
ps -p <PID> -o pid,cmd
```

### 正常停止

```bash
kill <PID>
```

### 备用端口查询

没有 `ss` 或看不到 PID 时运行：

```bash
lsof -nP -iTCP:5000 -sTCP:LISTEN
```

### 由服务管理器启动

如果服务由 systemd、Docker 或其他进程管理器启动，应通过对应管理器停止，避免它自动拉起新进程。

systemd：

```bash
systemctl stop <service-name>
```

Docker 查询容器：

```bash
docker ps
```

Docker 停止容器：

```bash
docker stop <container-name-or-id>
```

## 验证端口已经释放

重新运行对应系统的端口查询命令。没有监听结果即表示端口已经释放。也可访问
`http://127.0.0.1:5000/`，连接失败表示该地址已无服务响应。

如果端口仍被占用，不要直接结束不明进程；先通过 `Get-Process`、`tasklist` 或 `ps` 核对命令和 PID。
